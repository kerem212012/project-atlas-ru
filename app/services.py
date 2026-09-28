from __future__ import annotations

from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import boto3

from .config import settings


class LocalStorage:
	def __init__(self, directory: Path) -> None:
		self.directory = Path(directory)

	def save(self, content: bytes, extension: str, content_type: str) -> tuple[str, str]:
		self.directory.mkdir(parents=True, exist_ok=True)
		key = f"{uuid4()}{extension}"
		(self.directory / key).write_bytes(content)
		return key, f"/uploads/{key}"

	def delete(self, key: str) -> None:
		if Path(key).name != key:
			raise ValueError("Invalid storage key")
		(self.directory / key).unlink(missing_ok=True)


class S3Storage:
	def __init__(self) -> None:
		self.bucket = settings.s3_bucket
		self.client = boto3.client(
			"s3",
			endpoint_url=settings.s3_endpoint_url or None,
			region_name=settings.s3_region,
			aws_access_key_id=settings.s3_access_key or None,
			aws_secret_access_key=settings.s3_secret_key or None,
		)

	def save(self, content: bytes, extension: str, content_type: str) -> tuple[str, str]:
		key = f"{uuid4()}{extension}"
		self.client.upload_fileobj(
			BytesIO(content),
			self.bucket,
			key,
			ExtraArgs={"ContentType": content_type},
		)
		url = f"https://{self.bucket}.s3.{settings.s3_region}.amazonaws.com/{quote(key)}"
		return key, url

	def delete(self, key: str) -> None:
		self.client.delete_object(Bucket=self.bucket, Key=key)


class DemoProvider:
	def improve_description(self, description: str) -> str:
		return f"Outcome: {description.strip()}"


class GeminiProvider:
	def __init__(self) -> None:
		if not settings.gemini_api_key:
			raise RuntimeError("GEMINI_API_KEY is required for the Gemini backend")
		from google import genai

		self.client = genai.Client(api_key=settings.gemini_api_key)

	def improve_description(self, description: str) -> str:
		response = self.client.models.generate_content(
			model=settings.gemini_model,
			contents=(
				"Improve this project description. Keep it accurate, concise, and "
				f"focused on outcomes:\n\n{description}"
			),
		)
		if not response.text:
			raise RuntimeError("Gemini returned an empty project description")
		return response.text.strip()
