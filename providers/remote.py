from __future__ import annotations
import asyncio
import io
import json
import os
from typing import AsyncIterator
import structlog
from core.config import SourceConfig
from providers.base import BaseProvider, ProviderSnapshot, SourceFile
log: structlog.BoundLogger = structlog.get_logger(__name__)

class FTPProvider(BaseProvider):

    def __init__(self, config: SourceConfig) -> None:
        self._host = config.ftp_host or 'localhost'
        self._port = config.ftp_port
        self._user = os.environ.get(config.ftp_user_env, 'anonymous')
        self._password = os.environ.get(config.ftp_pass_env, '')
        self._path = config.ftp_path
        self._ignore = config.ignore_patterns
        self._poll_interval = config.poll_interval_seconds

    async def fetch_snapshot(self) -> ProviderSnapshot:
        try:
            import aioftp
            files: list[SourceFile] = []
            async with aioftp.Client.context(self._host, port=self._port, user=self._user, password=self._password) as client:
                await client.change_directory(self._path)
                async for path, info in client.list(recursive=True):
                    path_str = str(path)
                    if info['type'] != 'file':
                        continue
                    if not self._is_text_file(path_str):
                        continue
                    if self._matches_ignore(path_str, self._ignore):
                        continue
                    async with client.download_stream(path_str) as stream:
                        chunks = []
                        async for chunk in stream.iter_by_block(4096):
                            chunks.append(chunk)
                    content = b''.join(chunks).decode('utf-8', errors='replace')
                    parts = path_str.lstrip('/').split('/')
                    folder = parts[0] if len(parts) > 1 else ''
                    files.append(SourceFile(path=path_str, name=parts[-1], content=content, folder=folder))
            return ProviderSnapshot(files=files)
        except Exception as exc:
            log.error('ftp.error', host=self._host, error=str(exc))
            return ProviderSnapshot(error=str(exc))

    async def watch(self) -> AsyncIterator[ProviderSnapshot]:
        while True:
            await asyncio.sleep(self._poll_interval)
            yield (await self.fetch_snapshot())

class GDriveProvider(BaseProvider):

    def __init__(self, config: SourceConfig) -> None:
        self._folder_id = config.gdrive_folder_id or ''
        self._credentials_json = os.environ.get(config.gdrive_credentials_env, '')
        self._ignore = config.ignore_patterns
        self._poll_interval = config.poll_interval_seconds
        self._service: object | None = None

    def _build_service(self) -> object:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds_dict = json.loads(self._credentials_json)
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=['https://www.googleapis.com/auth/drive.readonly'])
        return build('drive', 'v3', credentials=creds, cache_discovery=False)

    async def fetch_snapshot(self) -> ProviderSnapshot:
        try:
            loop = asyncio.get_running_loop()
            files = await loop.run_in_executor(None, self._sync_fetch)
            return ProviderSnapshot(files=files)
        except Exception as exc:
            log.error('gdrive.error', folder=self._folder_id, error=str(exc))
            return ProviderSnapshot(error=str(exc))

    def _sync_fetch(self) -> list[SourceFile]:
        if self._service is None:
            self._service = self._build_service()
        service = self._service
        results: list[SourceFile] = []
        self._recurse(service, self._folder_id, '', results)
        return results

    def _recurse(self, service: object, folder_id: str, prefix: str, acc: list[SourceFile]) -> None:
        from googleapiclient.http import MediaIoBaseDownload
        page_token = None
        while True:
            resp = service.files().list(q=f"'{folder_id}' in parents and trashed=false", fields='nextPageToken,files(id,name,mimeType,size)', pageToken=page_token).execute()
            for item in resp.get('files', []):
                name: str = item['name']
                mime: str = item['mimeType']
                fid: str = item['id']
                path = f'{prefix}/{name}'.lstrip('/')
                if mime == 'application/vnd.google-apps.folder':
                    self._recurse(service, fid, path, acc)
                elif self._is_text_file(name) and (not self._matches_ignore(name, self._ignore)):
                    buf = io.BytesIO()
                    req = service.files().get_media(fileId=fid)
                    dl = MediaIoBaseDownload(buf, req)
                    done = False
                    while not done:
                        _, done = dl.next_chunk()
                    content = buf.getvalue().decode('utf-8', errors='replace')
                    folder = path.split('/')[0] if '/' in path else ''
                    acc.append(SourceFile(path=path, name=name, content=content, folder=folder))
            page_token = resp.get('nextPageToken')
            if not page_token:
                break

    async def watch(self) -> AsyncIterator[ProviderSnapshot]:
        while True:
            await asyncio.sleep(self._poll_interval)
            yield (await self.fetch_snapshot())

class OneDriveProvider(BaseProvider):
    GRAPH_BASE = 'https://graph.microsoft.com/v1.0'

    def __init__(self, config: SourceConfig) -> None:
        self._drive_id = config.onedrive_drive_id or ''
        self._folder_path = config.onedrive_folder_path
        self._client_id = os.environ.get(config.onedrive_client_id_env, '')
        self._client_secret = os.environ.get(config.onedrive_client_secret_env, '')
        self._tenant_id = os.environ.get(config.onedrive_tenant_id_env, 'common')
        self._ignore = config.ignore_patterns
        self._poll_interval = config.poll_interval_seconds
        self._token: str | None = None
        self._session = None

    async def _get_token(self) -> str:
        import msal
        app = msal.ConfidentialClientApplication(self._client_id, authority=f'https://login.microsoftonline.com/{self._tenant_id}', client_credential=self._client_secret)
        result = app.acquire_token_for_client(['https://graph.microsoft.com/.default'])
        if 'access_token' not in result:
            raise RuntimeError(f"MSAL error: {result.get('error_description')}")
        return result['access_token']

    async def _headers(self) -> dict[str, str]:
        if not self._token:
            self._token = await self._get_token()
        return {'Authorization': f'Bearer {self._token}'}

    async def fetch_snapshot(self) -> ProviderSnapshot:
        import aiohttp
        try:
            headers = await self._headers()
            files: list[SourceFile] = []
            async with aiohttp.ClientSession(headers=headers) as session:
                await self._recurse(session, None, '', files)
            return ProviderSnapshot(files=files)
        except Exception as exc:
            log.error('onedrive.error', error=str(exc))
            self._token = None
            return ProviderSnapshot(error=str(exc))

    async def _recurse(self, session: object, item_id: str | None, prefix: str, acc: list[SourceFile]) -> None:
        import aiohttp
        if item_id:
            url = f'{self.GRAPH_BASE}/drives/{self._drive_id}/items/{item_id}/children'
        else:
            encoded = self._folder_path.strip('/')
            url = f'{self.GRAPH_BASE}/drives/{self._drive_id}/root:/{encoded}:/children'
        while url:
            async with session.get(url) as resp:
                data = await resp.json()
            for item in data.get('value', []):
                name: str = item['name']
                path = f'{prefix}/{name}'.lstrip('/')
                if 'folder' in item:
                    await self._recurse(session, item['id'], path, acc)
                elif 'file' in item:
                    if not self._is_text_file(name) or self._matches_ignore(name, self._ignore):
                        continue
                    dl_url = item.get('@microsoft.graph.downloadUrl', '')
                    if not dl_url:
                        continue
                    async with session.get(dl_url) as dr:
                        raw = await dr.read()
                    content = raw.decode('utf-8', errors='replace')
                    folder = path.split('/')[0] if '/' in path else ''
                    acc.append(SourceFile(path=path, name=name, content=content, folder=folder))
            url = data.get('@odata.nextLink', '')

    async def watch(self) -> AsyncIterator[ProviderSnapshot]:
        while True:
            await asyncio.sleep(self._poll_interval)
            yield (await self.fetch_snapshot())