import io

import pytest
from ak5.config import settings
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_ticket_attachments_crud_flow(client: AsyncClient, auth_headers, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ATTACHMENTS_DIR", str(tmp_path / "attachments"))

    headers = auth_headers("user_pm", "human")

    # 1. Create a ticket
    t_resp = await client.post(
        "/api/v1/tickets",
        json={"title": "Ticket with Artifacts", "board_id": "proj-core-engine", "column_id": "col_todo"},
        headers=headers,
    )
    assert t_resp.status_code == 201
    ticket_id = t_resp.json()["ticket_id"]

    # 2. Upload an attachment
    file_content = b"# Benchmark Report\nExecution took 12ms.\n"
    files = {"file": ("benchmark_report.md", io.BytesIO(file_content), "text/markdown")}
    upload_resp = await client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        files=files,
        headers=headers,
    )
    assert upload_resp.status_code == 201, upload_resp.text
    att_data = upload_resp.json()
    assert att_data["filename"] == "benchmark_report.md"
    assert att_data["file_size"] == len(file_content)
    assert att_data["content_type"] == "text/markdown"
    assert att_data["actor_id"] == "user_pm"
    attachment_id = att_data["attachment_id"]

    # 3. Check ticket detail includes attachment
    detail_resp = await client.get(f"/api/v1/tickets/{ticket_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert len(detail["attachments"]) == 1
    assert detail["attachments"][0]["attachment_id"] == attachment_id
    assert detail["attachments"][0]["filename"] == "benchmark_report.md"

    # 4. List attachments endpoint
    list_resp = await client.get(f"/api/v1/tickets/{ticket_id}/attachments")
    assert list_resp.status_code == 200
    att_list = list_resp.json()
    assert len(att_list) == 1
    assert att_list[0]["attachment_id"] == attachment_id

    # 5. Download attachment
    dl_resp = await client.get(f"/api/v1/tickets/{ticket_id}/attachments/{attachment_id}")
    assert dl_resp.status_code == 200
    assert dl_resp.content == file_content
    assert "attachment" in dl_resp.headers.get("content-disposition", "")

    # 6. Delete attachment
    del_resp = await client.delete(
        f"/api/v1/tickets/{ticket_id}/attachments/{attachment_id}",
        headers=headers,
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    # 7. Verify it is gone
    dl_gone = await client.get(f"/api/v1/tickets/{ticket_id}/attachments/{attachment_id}")
    assert dl_gone.status_code == 404

    detail_after = (await client.get(f"/api/v1/tickets/{ticket_id}")).json()
    assert len(detail_after["attachments"]) == 0


@pytest.mark.asyncio
async def test_ticket_attachment_errors(client: AsyncClient, auth_headers, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ATTACHMENTS_DIR", str(tmp_path / "attachments"))
    monkeypatch.setattr(settings, "MAX_ATTACHMENT_SIZE_BYTES", 20)  # Very small limit: 20 bytes

    headers = auth_headers("user_pm", "human")

    # Non-existent ticket upload
    files = {"file": ("test.txt", io.BytesIO(b"short"), "text/plain")}
    resp_404 = await client.post("/api/v1/tickets/TK-99999/attachments", files=files, headers=headers)
    assert resp_404.status_code == 404

    # Create real ticket
    t_resp = await client.post(
        "/api/v1/tickets",
        json={"title": "Size limit test", "board_id": "proj-core-engine", "column_id": "col_todo"},
        headers=headers,
    )
    ticket_id = t_resp.json()["ticket_id"]

    # File exceeding limit (20 bytes limit, provide 50 bytes)
    big_content = b"A" * 50
    files_big = {"file": ("big.bin", io.BytesIO(big_content), "application/octet-stream")}
    resp_413 = await client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        files=files_big,
        headers=headers,
    )
    assert resp_413.status_code == 413


@pytest.mark.asyncio
async def test_ticket_attachment_traversal_filename_confined(
    client: AsyncClient, auth_headers, tmp_path, monkeypatch
):
    storage_dir = tmp_path / "attachments"
    monkeypatch.setattr(settings, "ATTACHMENTS_DIR", str(storage_dir))

    headers = auth_headers("user_pm", "human")
    t_resp = await client.post(
        "/api/v1/tickets",
        json={"title": "Traversal test", "board_id": "proj-core-engine", "column_id": "col_todo"},
        headers=headers,
    )
    ticket_id = t_resp.json()["ticket_id"]

    # Traversal filename
    files = {"file": ("../../../../etc/passwd", io.BytesIO(b"root:x:0:0"), "text/plain")}
    resp = await client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        files=files,
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["filename"] == "passwd"

    # Verify stored file is confined within storage_dir
    stored_files = list(storage_dir.iterdir())
    assert len(stored_files) == 1
    assert stored_files[0].is_relative_to(storage_dir.resolve())

