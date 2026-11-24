from fpdf import FPDF


def make_pdf(lines: list[str]) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for line in lines:
        pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


class FakeFetcher:
    def __init__(self, pages: dict[str, str], files: dict[str, bytes]):
        self.pages = pages
        self.files = files
        self.downloads: list[str] = []

    async def get_text(self, url: str) -> str:
        if url not in self.pages:
            raise ConnectionError("reset")
        return self.pages[url]

    async def get_bytes(self, url: str) -> bytes:
        self.downloads.append(url)
        return self.files[url]
