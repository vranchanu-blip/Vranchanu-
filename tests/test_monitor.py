import pytest

from bot.db import Database
from bot.modules.dosar.monitor import AncMonitor
from bot.modules.dosar.service import DosarService
from tests.helpers import FakeFetcher, make_pdf

STADIU = "https://anc/stadiu-dosar/"
ORDINE = "https://anc/ordine-articolul-11/"


@pytest.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


def pages(orders: list[str]) -> dict[str, str]:
    return {
        STADIU: '<a href="/s/art11-2021.pdf">Articolul 11 - 2021</a>',
        ORDINE: "".join(f'<a href="/o/{o}.pdf">Ordin {o}</a>' for o in orders),
    }


async def test_cycle_bootstrap_then_changes(db):
    files = {
        "https://anc/s/art11-2021.pdf": make_pdf(["100/RD/2021 01.02.2021", "200/RD/2021 02.02.2021"]),
        "https://anc/o/1.pdf": make_pdf(["1. A B 50/RD/2021", "2. C D 60/RD/2021"]),
    }
    fetcher = FakeFetcher(pages(["1"]), files)
    got = []

    async def on_result(r):
        got.append(r)

    mon = AncMonitor(db, fetcher, (STADIU, ORDINE), on_result=on_result, download_delay=0)

    r1 = await mon.run_cycle()
    assert r1.bootstrap and r1.pages_ok == 2 and r1.pdf_changed == 2
    assert r1.new_mentions == [] and r1.new_orders == []  # без спама при первой загрузке

    # Ничего не поменялось: приказ повторно не скачивается, stadiu скачивается, но без изменений
    fetcher.downloads.clear()
    r2 = await mon.run_cycle()
    assert fetcher.downloads == ["https://anc/s/art11-2021.pdf"]
    assert r2.new_mentions == [] and not r2.bootstrap

    # Обновился stadiu (у 200 появилось решение) и вышел новый приказ с номером 100
    files["https://anc/s/art11-2021.pdf"] = make_pdf(["100/RD/2021 01.02.2021", "200/RD/2021 02.02.2021 ordin 9/P"])
    files["https://anc/o/2.pdf"] = make_pdf(["1. X Y 100/RD/2021"])
    fetcher.pages = pages(["2", "1"])
    r3 = await mon.run_cycle()
    assert [o.url for o in r3.new_orders] == ["https://anc/o/2.pdf"]
    new = {(m.number, m.kind) for m in r3.new_mentions}
    assert new == {("200/RD/2021", "stadiu"), ("100/RD/2021", "ordin")}

    svc = DosarService(db)
    hist = await svc.mentions("200/RD/2021")
    assert [h["active"] for h in hist] == [1, 0]  # старая строка помечена неактивной
    prog = await svc.year_progress("100/RD/2021")
    assert prog.in_orders == 3 and prog.max_seq_in_orders == 100 and prog.position_hint == 2
    assert len(got) == 3


async def test_site_down_does_not_bootstrap(db):
    mon = AncMonitor(db, FakeFetcher({}, {}), (STADIU,), download_delay=0)
    r = await mon.run_cycle()
    assert r.pages_ok == 0 and r.errors
    assert await db.fetchall("SELECT * FROM anc_documents") == []


async def test_dosar_service_limits(db):
    await db.upsert_user(1, "u", "U")
    svc = DosarService(db)
    assert await svc.add(1, "1/RD/2020", None)
    assert await svc.add(1, "1/RD/2020", "dup") is None
    for i in range(2, 11):
        assert await svc.add(1, f"{i}/RD/2020", None)
    assert await svc.add(1, "99/RD/2020", None) is None
    assert await svc.toggle_orders(1) is False


async def test_renamed_stadiu_list_and_new_page(db):
    old = "https://anc/s/Art-11-2022-update-01.10.2026.pdf"
    new = "https://anc/s/Art-11-2022-update-10.11.2026.pdf"
    files = {
        old: make_pdf(["500/RD/2022 05.05.2022", "501/RD/2022 06.05.2022"]),
        new: make_pdf(["500/RD/2022 05.05.2022", "501/RD/2022 06.05.2022 ordin 77/P"]),
        "https://anc/o/9.pdf": make_pdf(["1. Q W 500/RD/2022"]),
    }
    fetcher = FakeFetcher({STADIU: f'<a href="{old}">Art 11 2022</a>'}, files)
    mon = AncMonitor(db, fetcher, (STADIU,), download_delay=0)
    await mon.run_cycle()

    # ANC выложил обновлённый список под новым именем, старый убран со страницы
    fetcher.pages[STADIU] = f'<a href="{new}">Art 11 2022</a>'
    r = await mon.run_cycle()
    assert [(m.number, m.line) for m in r.new_mentions] == [("501/RD/2022", "501/RD/2022 06.05.2022 ordin 77/P")]

    svc = DosarService(db)
    active = await svc.mentions("500/RD/2022", active_only=True)
    assert [a["url"] for a in active] == [new]

    # Добавили новую страницу-источник: её архив не должен прийти как «новые приказы»
    fetcher.pages[ORDINE] = '<a href="/o/9.pdf">Ordin 9</a>'
    mon.pages = (STADIU, ORDINE)
    r = await mon.run_cycle()
    assert r.new_orders == [] and r.new_mentions == []
    assert (await svc.year_progress("500/RD/2022")).in_orders == 1


async def test_interrupted_bootstrap_stays_silent(db):
    files = {"https://anc/s/a.pdf": make_pdf(["1/RD/2022"]), "https://anc/s/b.pdf": make_pdf(["2/RD/2022"])}
    fetcher = FakeFetcher({STADIU: '<a href="/s/a.pdf">a</a><a href="/s/b.pdf">b</a>'}, files)
    mon = AncMonitor(db, fetcher, (STADIU,), download_delay=0)

    real = fetcher.get_bytes
    async def crash_on_b(url):
        if url.endswith("b.pdf"):
            raise KeyboardInterrupt  # бот остановили посреди первой загрузки
        return await real(url)
    fetcher.get_bytes = crash_on_b
    with pytest.raises(KeyboardInterrupt):
        await mon.run_cycle()

    fetcher.get_bytes = real
    r = await mon.run_cycle()
    assert r.bootstrap and r.new_mentions == []
    r = await mon.run_cycle()
    assert not r.bootstrap
