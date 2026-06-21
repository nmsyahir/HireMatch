from pathlib import Path

import PyPDF2
from PIL import Image, UnidentifiedImageError
from django.core.exceptions import ValidationError


MAX_RESUME_SIZE = 5 * 1024 * 1024
MAX_IMAGE_SIZE = 2 * 1024 * 1024
MAX_RESUME_PAGES = 30
MAX_IMAGE_DIMENSION = 5000


def validate_resume_upload(uploaded_file):
    if uploaded_file.size > MAX_RESUME_SIZE:
        raise ValidationError('Resume must be 5 MB or smaller.')
    if Path(uploaded_file.name).suffix.lower() != '.pdf':
        raise ValidationError('Resume must be a PDF file.')
    if uploaded_file.content_type not in {'application/pdf', 'application/octet-stream'}:
        raise ValidationError('Invalid PDF content type.')

    header = uploaded_file.read(5)
    uploaded_file.seek(0)
    if header != b'%PDF-':
        raise ValidationError('The uploaded file is not a valid PDF.')

    try:
        reader = PyPDF2.PdfReader(uploaded_file)
        if reader.is_encrypted:
            raise ValidationError('Password-protected PDFs are not supported.')
        if not reader.pages or len(reader.pages) > MAX_RESUME_PAGES:
            raise ValidationError(f'Resume must contain between 1 and {MAX_RESUME_PAGES} pages.')
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError('The uploaded PDF is damaged or unreadable.') from exc
    finally:
        uploaded_file.seek(0)


def validate_image_upload(uploaded_file):
    if uploaded_file.size > MAX_IMAGE_SIZE:
        raise ValidationError('Image must be 2 MB or smaller.')
    if Path(uploaded_file.name).suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
        raise ValidationError('Use a JPG, PNG, or WebP image.')
    if uploaded_file.content_type not in {'image/jpeg', 'image/png', 'image/webp'}:
        raise ValidationError('Invalid image content type.')

    try:
        image = Image.open(uploaded_file)
        width, height = image.size
        image.verify()
        if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
            raise ValidationError('Image dimensions must not exceed 5000 x 5000 pixels.')
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError('The uploaded image is damaged or invalid.') from exc
    finally:
        uploaded_file.seek(0)
