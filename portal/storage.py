import os
import time

import cloudinary.api
import cloudinary.uploader
import cloudinary.utils
import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateResumeStorage(Storage):
    def __init__(self):
        self.local_storage = FileSystemStorage(
            location=settings.PRIVATE_MEDIA_ROOT,
            base_url=None,
        )

    @property
    def uses_cloudinary(self):
        return bool(os.environ.get('CLOUDINARY_URL'))

    def _open(self, name, mode='rb'):
        if not self.uses_cloudinary:
            return self.local_storage.open(name, mode)

        download_url = cloudinary.utils.private_download_url(
            name,
            None,
            type='authenticated',
            resource_type='raw',
            expires_at=int(time.time()) + 60,
        )
        response = requests.get(download_url, timeout=30)
        response.raise_for_status()
        file = ContentFile(response.content)
        file.name = name
        return file

    def _save(self, name, content):
        if not self.uses_cloudinary:
            return self.local_storage.save(name, content)

        content.seek(0)
        response = cloudinary.uploader.upload(
            content,
            resource_type='raw',
            type='authenticated',
            folder='hirematch/private_resumes',
            use_filename=True,
            unique_filename=True,
            overwrite=False,
        )
        return response['public_id']

    def exists(self, name):
        if not self.uses_cloudinary:
            return self.local_storage.exists(name)
        return False

    def delete(self, name):
        if not self.uses_cloudinary:
            return self.local_storage.delete(name)
        return cloudinary.uploader.destroy(
            name,
            resource_type='raw',
            type='authenticated',
            invalidate=True,
        )

    def size(self, name):
        if not self.uses_cloudinary:
            return self.local_storage.size(name)
        resource = cloudinary.api.resource(
            name,
            resource_type='raw',
            type='authenticated',
        )
        return resource.get('bytes', 0)

    def url(self, name):
        raise ValueError('Private resume files do not have public URLs.')


private_resume_storage = PrivateResumeStorage()
