from bot.modules.dosar.anc import AncClient, classify, extract_pdf_links, find_mentions, normalize_number, pdf_to_text
from tests.helpers import make_pdf


def test_normalize_number():
    assert normalize_number("12345/RD/2021") == "12345/RD/2021"
    assert normalize_number(" 00123 / rd / 2019 ") == "123/RD/2019"
    assert normalize_number("12345/2021") is None
    assert normalize_number("abc") is None


def test_extract_pdf_links_dedup_relative_and_filter():
    html = """
      <a href="/wp-content/uploads/2024/05/Ordin-123-P-din-10.05.2024.pdf">Ordin <b>123/P</b> din 10.05.2024</a>
      <a href='https://cetatenie.just.ro/wp-content/uploads/2024/05/Ordin-123-P-din-10.05.2024.pdf'>dup</a>
      <a href="/stadiu/Art-11-2021.pdf">Articolul 11 &ndash; 2021</a>
      <a href="/page">not pdf</a>
    """
    links = extract_pdf_links(html, "https://cetatenie.just.ro/ordine-articolul-11/")
    assert [l.url for l in links] == [
        "https://cetatenie.just.ro/wp-content/uploads/2024/05/Ordin-123-P-din-10.05.2024.pdf",
        "https://cetatenie.just.ro/stadiu/Art-11-2021.pdf",
    ]
    assert links[0].title == "Ordin 123 /P din 10.05.2024" or links[0].title.startswith("Ordin")
    assert links[1].title == "Articolul 11 – 2021"
    only = extract_pdf_links(html, "https://x/", url_filter="2021")
    assert len(only) == 1


def test_classify():
    assert classify("https://x/Ordin-1.pdf", "", "https://x/stadiu-dosar/") == "ordin"
    assert classify("https://x/2021.pdf", "Articolul 11", "https://x/stadiu-dosar/") == "stadiu"
    assert classify("https://x/a.pdf", "", "https://x/ordine-articolul-11/") == "ordin"
    assert classify("https://x/a.pdf", "", "https://x/other/") == "other"


def test_find_mentions_from_real_pdf():
    data = make_pdf([
        "Nr. dosar   Data inregistrarii   Termen   Solutie",
        "12345/RD/2021   10.03.2021   15.09.2024   ordin 555/P/2024",
        "12346 / RD / 2021   11.03.2021",
        "1. POPESCU ION (VASILE) 777/RD/2019; 2. IONESCU ANA 778/RD/2019",
    ])
    found = find_mentions(pdf_to_text(data))
    assert set(found) == {"12345/RD/2021", "12346/RD/2021", "777/RD/2019", "778/RD/2019"}
    assert any("ordin 555/P/2024" in line for line in found["12345/RD/2021"])


async def test_relay_url():
    c = AncClient()
    assert c._via_relay("https://cetatenie.just.ro/a b.pdf") == "https://cetatenie.just.ro/a b.pdf"
    c.relay = "https://anc.example.workers.dev/"
    assert c._via_relay("https://cetatenie.just.ro/x.pdf?v=1") == (
        "https://anc.example.workers.dev/?url=https%3A%2F%2Fcetatenie.just.ro%2Fx.pdf%3Fv%3D1"
    )
    await c.close()
