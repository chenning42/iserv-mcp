from iserv_mcp.attachment_parser import parse_parentletter_attachments


def test_extracts_download_and_file_links_only():
    html = """
    <a href="/iserv/dashboard">Home</a>
    <a href="/iserv/parentletter/attachment/abc/report.pdf">Bericht.pdf</a>
    <a download href="/iserv/blob/xyz">Einladung.docx</a>
    """
    items = parse_parentletter_attachments(html)
    assert [(item.href, item.filename) for item in items] == [
        ("/iserv/parentletter/attachment/abc/report.pdf", "Bericht.pdf"),
        ("/iserv/blob/xyz", "Einladung.docx"),
    ]


def test_deduplicates_and_sanitizes_filename():
    html = """
    <a href="/iserv/file/one">../Plan.pdf</a>
    <a href="/iserv/file/one">duplicate</a>
    """
    items = parse_parentletter_attachments(html)
    assert len(items) == 1
    assert items[0].filename == "_Plan.pdf"
