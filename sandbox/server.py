import base64
import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches

PAPER_PROXY = os.environ.get('PAPER_PROXY', '').rstrip('/')
PPT_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'

mcp = FastMCP('ppt-read-write')
_client = httpx.Client(verify=False, timeout=120.0)


def _proxy_json(operation: str, payload: Optional[Dict[str, Any]] = None, file_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not PAPER_PROXY:
        return None
    url = f'{PAPER_PROXY}/{operation}'
    try:
        if file_path:
            with open(file_path, 'rb') as fh:
                response = _client.post(
                    url,
                    files={'file': (Path(file_path).name, fh, PPT_MIME)},
                    data=payload or {},
                )
        else:
            response = _client.post(url, json=payload or {})
        response.raise_for_status()
        try:
            return response.json()
        except Exception:
            return {'ok': False, 'error': 'proxy response is not JSON', 'text': response.text[:500]}
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}


def _load_source_bytes(source: str) -> bytes:
    if source.startswith('http://') or source.startswith('https://'):
        response = _client.get(source)
        response.raise_for_status()
        return response.content
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f'PPT file not found: {source}')
    return path.read_bytes()


def _collect_shape_text(shape) -> List[str]:
    texts: List[str] = []
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for child in shape.shapes:
            texts.extend(_collect_shape_text(child))
    if shape.has_text_frame:
        for paragraph in shape.text_frame.paragraphs:
            line = paragraph.text.strip()
            if line:
                texts.append(line)
    if shape.has_table:
        for row in shape.table.rows:
            cells = [' '.join(cell.text.split()) for cell in row.cells]
            if any(cells):
                texts.append(' | '.join(cells))
    return texts


def _parse_pptx_data(data: bytes) -> Dict[str, Any]:
    prs = Presentation(io.BytesIO(data))
    slides_payload: List[Dict[str, Any]] = []
    for index, slide in enumerate(prs.slides, start=1):
        shape_texts: List[str] = []
        for shape in slide.shapes:
            shape_texts.extend(_collect_shape_text(shape))
        notes = ''
        try:
            notes = slide.notes_slide.notes_text_frame.text.strip()
        except Exception:
            notes = ''
        slides_payload.append({
            'slide_number': index,
            'texts': shape_texts,
            'notes': notes,
        })
    return {'slide_count': len(prs.slides), 'slides': slides_payload}


def _create_pptx_bytes(slides: List[Dict[str, Any]]) -> bytes:
    prs = Presentation()
    layout = prs.slide_layouts[1]
    for item in slides:
        if not isinstance(item, dict):
            raise ValueError('each slide must be an object')
        slide = prs.slides.add_slide(layout)
        title_shape = slide.shapes.title
        if title_shape is not None:
            title_shape.text = str(item.get('title', ''))
        body_placeholder = None
        for placeholder in slide.placeholders:
            if placeholder.placeholder_format.idx == 1:
                body_placeholder = placeholder
                break
        if body_placeholder is None:
            body_placeholder = slide.shapes.add_textbox(Inches(1), Inches(1.5), Inches(8), Inches(5))
        text_frame = body_placeholder.text_frame
        text_frame.clear()
        lines: List[str] = []
        if item.get('bullets'):
            lines = [str(bullet) for bullet in item['bullets']]
        elif item.get('content'):
            lines = str(item['content']).splitlines()
        if lines:
            text_frame.text = lines[0]
            for line in lines[1:]:
                paragraph = text_frame.add_paragraph()
                paragraph.text = line
        else:
            text_frame.text = ''
    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


@mcp.tool()
def read_ppt(source: str, include_notes: bool = True) -> str:
    '''读取 PowerPoint 文件并返回每张幻灯片的文字信息。

    Args:
        source: 本地 .pptx 文件路径或 http(s) 下载地址。
        include_notes: 是否包含演讲者备注。
    '''
    proxy_result = None
    if PAPER_PROXY:
        payload = {'include_notes': str(include_notes).lower()}
        if source.startswith('http://') or source.startswith('https://'):
            proxy_result = _proxy_json('ppt/read', payload={'url': source, 'include_notes': str(include_notes).lower()})
        else:
            proxy_result = _proxy_json('ppt/read', payload=payload, file_path=source)
        if proxy_result is not None and proxy_result.get('ok', True) is not False:
            return json.dumps({'source': source, **proxy_result}, ensure_ascii=False, indent=2)
    try:
        data = _load_source_bytes(source)
        result = _parse_pptx_data(data)
        if not include_notes:
            for slide in result['slides']:
                slide['notes'] = ''
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False)


@mcp.tool()
def write_ppt(output_path: str, slides: List[Dict[str, Any]], overwrite: bool = True) -> str:
    '''根据结构化内容生成 PowerPoint(.pptx) 文件并写入本地。

    Args:
        output_path: 要写入的 .pptx 文件路径。
        slides: 幻灯片列表，每项可包含 title、bullets 或 content。
        overwrite: 文件已存在时是否允许覆盖。
    '''
    if not isinstance(slides, list):
        return json.dumps({'ok': False, 'error': 'slides must be a list'}, ensure_ascii=False)
    path = Path(output_path)
    if path.exists() and not overwrite:
        return json.dumps({'ok': False, 'error': f'{path} already exists and overwrite is False'}, ensure_ascii=False)

    if PAPER_PROXY:
        proxy_result = _proxy_json('ppt/write', payload={'output_path': str(path), 'slides': slides, 'overwrite': overwrite})
        if proxy_result is not None and proxy_result.get('ok', True) is not False:
            encoded = proxy_result.get('file_base64') or proxy_result.get('base64') or proxy_result.get('data')
            if encoded:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(base64.b64decode(encoded))
                    return json.dumps({'ok': True, 'file': str(path), 'mode': 'proxy', 'proxy': proxy_result}, ensure_ascii=False, indent=2)
                except Exception as exc:
                    return json.dumps({'ok': False, 'error': 'proxy returned invalid base64: ' + str(exc)}, ensure_ascii=False)
            proxy_path = proxy_result.get('path')
            if proxy_path and Path(proxy_path).exists():
                return json.dumps({'ok': True, 'file': str(path), 'mode': 'proxy', 'proxy': proxy_result}, ensure_ascii=False, indent=2)

    try:
        data = _create_pptx_bytes(slides)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': 'write_ppt local failed: ' + str(exc)}, ensure_ascii=False)
    return json.dumps({'ok': True, 'file': str(path), 'mode': 'local'}, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    mcp.run()