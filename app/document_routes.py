import base64
import io
import re
from flask import Blueprint, request, jsonify, g, make_response, current_app
import requests as req_lib
from .auth import require_auth
from .db import db
from .models import Document, Person, Tree, User
from . import limiter

document_bp = Blueprint('documents', __name__)

MAX_FILE_BYTES = 8 * 1024 * 1024  # 8 MB

ALLOWED_MIME = {
    'image/jpeg', 'image/jpg', 'image/png', 'image/tiff',
    'image/webp', 'image/gif', 'image/heic', 'image/heif',
    'application/pdf',
    'text/plain',
}

IMAGE_MIME = {'image/jpeg', 'image/jpg', 'image/png', 'image/tiff',
              'image/webp', 'image/gif', 'image/heic', 'image/heif'}


def _person_owned(person_id: int) -> Person | None:
    p = db.session.get(Person, person_id)
    if not p:
        return None
    tree = db.session.get(Tree, p.tree_id)
    if not tree or tree.user_id != g.user_id:
        return None
    return p


def _extract_text_from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text.strip())
        return '\n\n'.join(pages)
    except Exception:
        return ''


def _ai_read_document(mime_type: str, file_data: bytes,
                       filename: str, person_name: str) -> dict:
    """
    Send document to Claude via OpenRouter.
    Images and handwritten notes use vision.
    PDFs and text use the extracted text.
    Returns {'extracted_text': ..., 'ai_summary': ...}
    """
    api_key = current_app.config.get('OPENROUTER_API_KEY', '')
    if not api_key:
        return {'extracted_text': '', 'ai_summary': ''}

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    system_prompt = (
        f"You are a genealogy research assistant analyzing a document about {person_name}. "
        "Extract all genealogy-relevant information: full names, birth dates and places, "
        "death dates and places, marriage details, occupations, relationships, locations, "
        "and any other facts that could help trace family history. "
        "If the document is handwritten, transcribe it first, then extract the facts. "
        "Be specific and factual — do not invent information."
    )

    if mime_type in IMAGE_MIME:
        # Vision model for images (including handwritten notes and scanned docs)
        b64 = base64.b64encode(file_data).decode()
        messages = [{
            'role': 'user',
            'content': [
                {
                    'type': 'image_url',
                    'image_url': {'url': f'data:{mime_type};base64,{b64}'},
                },
                {
                    'type': 'text',
                    'text': (
                        'Please read this document. '
                        'If it is handwritten, transcribe the full text first. '
                        'Then list every genealogy fact you can find: names, dates, '
                        'places, relationships, occupations.'
                    ),
                },
            ],
        }]
        model = 'anthropic/claude-3.5-haiku'
    else:
        # Text model for PDFs and plain text
        if mime_type == 'application/pdf':
            content = _extract_text_from_pdf(file_data)
        else:
            content = file_data.decode('utf-8', errors='replace')

        if not content.strip():
            return {'extracted_text': '', 'ai_summary': 'Could not extract text from document.'}

        messages = [{
            'role': 'user',
            'content': (
                f'Document: {filename}\n\n'
                f'{content[:6000]}\n\n'
                'List every genealogy fact you can find in this document: '
                'names, dates, places, relationships, occupations.'
            ),
        }]
        model = 'anthropic/claude-3-haiku'

    try:
        resp = req_lib.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers=headers,
            json={
                'model': model,
                'messages': [{'role': 'system', 'content': system_prompt}] + messages,
                'max_tokens': 600,
            },
            timeout=30,
        )
        resp.raise_for_status()
        summary = resp.json()['choices'][0]['message']['content'].strip()

        extracted = content if mime_type not in IMAGE_MIME else ''
        return {'extracted_text': extracted, 'ai_summary': summary}
    except Exception:
        return {'extracted_text': '', 'ai_summary': ''}


@document_bp.post('/api/persons/<int:person_id>/documents')
@require_auth
@limiter.limit('30 per hour')
def upload_document(person_id):
    person = _person_owned(person_id)
    if not person:
        return jsonify({'error': 'Person not found'}), 404

    user = db.session.get(User, g.user_id)
    if user.total_tokens() < 5:
        return jsonify({'error': 'Insufficient tokens. Document analysis costs 5 tokens.'}), 402

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'No file selected'}), 400

    data = f.read()
    if len(data) > MAX_FILE_BYTES:
        return jsonify({'error': 'File too large. Maximum size is 8 MB.'}), 413

    mime = f.mimetype or 'application/octet-stream'
    # Normalise common variants
    if mime == 'image/jpg':
        mime = 'image/jpeg'
    if mime not in ALLOWED_MIME:
        return jsonify({
            'error': 'Unsupported file type. Upload JPEG, PNG, TIFF, WEBP, PDF, or plain text.'
        }), 415

    # Sanitise filename
    safe_name = re.sub(r'[^\w.\-]', '_', f.filename)[:200]
    person_name = f'{person.first_name or ""} {person.last_name or ""}'.strip() or 'this person'

    # AI reads the document first — only deduct tokens if the call succeeds
    extraction = _ai_read_document(mime, data, safe_name, person_name)

    user.deduct_tokens(5)
    db.session.commit()

    doc = Document(
        person_id=person_id,
        tree_id=person.tree_id,
        filename=safe_name,
        mime_type=mime,
        file_data=data,
        file_size=len(data),
        extracted_text=extraction['extracted_text'],
        ai_summary=extraction['ai_summary'],
    )
    db.session.add(doc)
    db.session.commit()

    return jsonify({
        'id': doc.id,
        'filename': doc.filename,
        'mime_type': doc.mime_type,
        'file_size': doc.file_size,
        'ai_summary': doc.ai_summary,
        'uploaded_at': doc.uploaded_at.isoformat(),
    }), 201


@document_bp.get('/api/persons/<int:person_id>/documents')
@require_auth
def list_documents(person_id):
    person = _person_owned(person_id)
    if not person:
        return jsonify({'error': 'Person not found'}), 404
    docs = Document.query.filter_by(person_id=person_id).order_by(Document.uploaded_at.desc()).all()
    return jsonify([{
        'id': d.id,
        'filename': d.filename,
        'mime_type': d.mime_type,
        'file_size': d.file_size,
        'ai_summary': d.ai_summary,
        'uploaded_at': d.uploaded_at.isoformat(),
    } for d in docs])


@document_bp.get('/api/documents/<int:doc_id>')
@require_auth
def get_document(doc_id):
    doc = db.session.get(Document, doc_id)
    if not doc:
        return jsonify({'error': 'Not found'}), 404
    person = _person_owned(doc.person_id)
    if not person:
        return jsonify({'error': 'Not found'}), 404
    resp = make_response(doc.file_data)
    resp.headers['Content-Type'] = doc.mime_type
    resp.headers['Content-Disposition'] = f'inline; filename="{doc.filename}"'
    return resp


@document_bp.delete('/api/documents/<int:doc_id>')
@require_auth
def delete_document(doc_id):
    doc = db.session.get(Document, doc_id)
    if not doc:
        return jsonify({'error': 'Not found'}), 404
    if not _person_owned(doc.person_id):
        return jsonify({'error': 'Not found'}), 404
    db.session.delete(doc)
    db.session.commit()
    return jsonify({'ok': True})
