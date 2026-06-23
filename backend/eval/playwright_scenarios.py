"""
Headed Playwright scenario runner for ARIA voice-inventory.

Runs all scenarios in a visible Chrome window so you can watch.
Each scenario types into the chat, waits for ARIA's response, and prints results.

Workspace: New York → Fridge
"""

import asyncio
from playwright.async_api import async_playwright

APP_URL          = "http://localhost:5173"
LOCATION_NAME    = "New York"
STORAGE_NAME     = "Fridge"
RESPONSE_TIMEOUT = 35_000   # ms
BETWEEN_PAUSE    = 1.2      # seconds between scenarios

# Selectors confirmed by DOM inspection
SEL_CHAT_INPUT = 'input[placeholder="Type or speak your inventory update..."]'
SEL_ARIA_MSG   = "p.text-gray-700"

results = []
PASS, FAIL = "✅", "❌"


# ── Helpers ───────────────────────────────────────────────────────────────────

async def setup_workspace(page):
    await page.goto(APP_URL, wait_until="networkidle")
    await page.wait_for_timeout(2000)

    # Click the indigo FAB (bottom-right circle)
    btns = await page.locator("button").all()
    for b in btns:
        cls = await b.get_attribute("class") or ""
        if "rounded-full" in cls and "indigo" in cls and "bottom" in cls:
            await b.click()
            break
    await page.wait_for_timeout(1200)

    # Step 1 — pick location (use button inside the z-50 modal, not background table cells)
    await page.locator("button", has_text=LOCATION_NAME).first.click()
    await page.wait_for_timeout(400)
    await page.locator("button", has_text="Next Step").click()
    await page.wait_for_timeout(800)

    # Step 2 — pick storage area
    await page.locator("button", has_text=STORAGE_NAME).first.click()
    await page.wait_for_timeout(400)
    await page.locator("button", has_text="Start Counting").click()

    # Wait for ARIA greeting to arrive
    await page.wait_for_selector(SEL_ARIA_MSG, timeout=15_000)
    await page.wait_for_timeout(1000)
    print(f"  Workspace: {LOCATION_NAME} → {STORAGE_NAME} — chat ready\n")


async def send(page, text: str) -> str:
    """Type a message, press Enter, wait for a new ARIA message, return its text."""
    before = await page.locator(SEL_ARIA_MSG).count()

    inp = page.locator(SEL_CHAT_INPUT)
    await inp.wait_for(state="visible", timeout=8000)
    await inp.click()
    await inp.fill(text)
    await page.keyboard.press("Enter")

    # Wait for a new paragraph to appear
    try:
        await page.wait_for_function(
            f"document.querySelectorAll('{SEL_ARIA_MSG}').length > {before}",
            timeout=RESPONSE_TIMEOUT,
        )
        # Give it a little more time to fully render
        await page.wait_for_timeout(600)
    except Exception:
        return "(timeout)"

    msgs = await page.locator(SEL_ARIA_MSG).all_text_contents()
    return msgs[-1].strip() if msgs else "(empty)"


def check(label: str, response: str,
          all_of: list[str] = None,
          any_of: list[str] = None,
          none_of: list[str] = None) -> bool:
    """
    all_of  — every keyword in this list must appear (use for item names, action words)
    any_of  — at least one keyword must appear (use for question/clarification phrases)
    none_of — none of these may appear
    """
    lo = response.lower()
    ok = True
    if all_of:
        ok = ok and all(k.lower() in lo for k in all_of)
    if any_of:
        ok = ok and any(k.lower() in lo for k in any_of)
    if none_of:
        ok = ok and not any(k.lower() in lo for k in none_of)
    icon = PASS if ok else FAIL
    results.append((icon, label, response[:120]))
    print(f"  {icon}  {label}")
    print(f"       ↳ {response[:110]}")
    return ok


# ── Scenario suite ────────────────────────────────────────────────────────────

async def run(page):
    sep = "─" * 68
    print(f"\n{'═'*68}")
    print("  ARIA Playwright Suite  ·  pgvector local memory  ·  New York/Fridge")
    print(f"{'═'*68}\n")

    # S1 ── Basic add + confirm ────────────────────────────────────────────────
    print(f"{sep}\nS1  Basic add + confirm")
    r = await send(page, "add 5 kg chicken to fridge")
    check("S1a  confirm prompt", r, all_of=["chicken"], none_of=["done", "added"])
    r = await send(page, "yes")
    check("S1b  item written", r, all_of=["chicken"], none_of=["confirm?"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S2 ── Cancel ─────────────────────────────────────────────────────────────
    print(f"{sep}\nS2  Cancel / deny")
    r = await send(page, "add 3 kg salmon")
    check("S2a  confirm prompt", r, all_of=["salmon"])
    r = await send(page, "no cancel that")
    check("S2b  cancelled", r,
          any_of=["cancel", "okay", "no problem", "got it", "sure", "cleared", "understood"],
          none_of=["added", "done"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S3 ── Non-food rejection ─────────────────────────────────────────────────
    print(f"{sep}\nS3  Non-food item rejection")
    r = await send(page, "add 10 pens to fridge")
    check("S3   non-food blocked", r, all_of=["pen"],
          none_of=["added", "confirm?", "done"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S4 ── Quantity without item ──────────────────────────────────────────────
    print(f"{sep}\nS4  Quantity given but no item name")
    r = await send(page, "add 20 kg")
    check("S4   asks for item", r,
          any_of=["what", "which", "item", "specify", "tell me", "?"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S5 ── Item without quantity ──────────────────────────────────────────────
    print(f"{sep}\nS5  Item given but no quantity")
    r = await send(page, "add butter")
    check("S5   asks for qty", r,
          all_of=["butter"],
          any_of=["how much", "quantity", "many", "amount", "?"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S6 ── Suspicious quantity ───────────────────────────────────────────────
    print(f"{sep}\nS6  Suspicious quantity (500 kg)")
    r = await send(page, "add 500 kg rice")
    check("S6a  warns or confirms", r, all_of=["rice"])
    if "confirm" in r.lower() or "?" in r:
        r2 = await send(page, "yes")
        check("S6b  confirmed", r2, all_of=["rice"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S7 ── Storage mismatch ───────────────────────────────────────────────────
    print(f"{sep}\nS7  Storage mismatch — dry goods in Fridge")
    r = await send(page, "add 2 kg flour to fridge")
    check("S7   warns or asks", r, all_of=["flour"])
    if "confirm" in r.lower() or "?" in r:
        r2 = await send(page, "yes")
        check("S7b  confirmed", r2, all_of=["flour"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S8 ── Cross-item qty bleed guard ────────────────────────────────────────
    print(f"{sep}\nS8  Cross-item qty bleed guard")
    r = await send(page, "add 8 kg lamb to fridge")
    check("S8a  confirm lamb", r, all_of=["lamb"])
    if "confirm" in r.lower() or "?" in r:
        r = await send(page, "yes")
        check("S8b  lamb written", r, all_of=["lamb"])
    r = await send(page, "also add tamarind")
    check("S8c  no qty bleed", r,
          all_of=["tamarind"],
          any_of=["how much", "quantity", "amount", "?"],
          none_of=["8 kg", "8.0", "lamb"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S9 ── Inventory query ────────────────────────────────────────────────────
    print(f"{sep}\nS9  Inventory query")
    r = await send(page, "what chicken do we have?")
    check("S9   query answered", r, all_of=["chicken"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S10 ── Multi-item add ───────────────────────────────────────────────────
    print(f"{sep}\nS10  Multi-item add")
    r = await send(page, "add 2 kg butter and 5 litres milk")
    check("S10a multi-item confirm", r, all_of=["butter", "milk"])
    if "confirm" in r.lower() or "?" in r:
        r = await send(page, "confirm")
        check("S10b multi-item done", r, all_of=["butter", "milk"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S11 ── Gibberish ─────────────────────────────────────────────────────────
    print(f"{sep}\nS11  Gibberish input")
    r = await send(page, "zxqwerty asdf jkl pqrs")
    check("S11  graceful handle", r,
          any_of=["clarify", "?", "what", "sorry", "understand", "didn", "sure", "mean"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S12 ── Affirmation variants ──────────────────────────────────────────────
    print(f"{sep}\nS12  Affirmation variants")
    r = await send(page, "add 3 kg tuna")
    check("S12a confirm prompt", r, all_of=["tuna"])
    if "confirm" in r.lower() or "?" in r:
        r = await send(page, "yep go ahead")
        check("S12b yep accepted", r, all_of=["tuna"], none_of=["confirm?"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S13 ── Remove / subtract ────────────────────────────────────────────────
    print(f"{sep}\nS13  Remove / subtract")
    r = await send(page, "remove 1 kg chicken from fridge")
    check("S13a remove prompt", r, all_of=["chicken"])
    if "confirm" in r.lower() or "?" in r:
        r = await send(page, "yes")
        check("S13b removed", r, all_of=["chicken"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S14 ── Fractional quantity ───────────────────────────────────────────────
    print(f"{sep}\nS14  Fractional quantity")
    r = await send(page, "add 0.5 kg saffron")
    check("S14a fractional", r, all_of=["saffron", "0.5"])
    if "confirm" in r.lower() or "?" in r:
        r = await send(page, "yes")
        check("S14b confirmed", r, all_of=["saffron"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S15 ── Unit abbreviation ─────────────────────────────────────────────────
    print(f"{sep}\nS15  Unit abbreviations (ltrs)")
    r = await send(page, "add 2 ltrs olive oil")
    check("S15a unit parsed", r, all_of=["oil"],
          any_of=["litr", "ltr", "2", "liters", "litres"])
    if "confirm" in r.lower() or "?" in r:
        r = await send(page, "yes")
        check("S15b confirmed", r, all_of=["oil"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S16 ── Analytics / value query ───────────────────────────────────────────
    print(f"{sep}\nS16  Total value query")
    r = await send(page, "what is the total value of our inventory?")
    check("S16  value query", r,
          any_of=["$", "value", "worth", "total"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S17 ── Stock-take phrasing ───────────────────────────────────────────────
    print(f"{sep}\nS17  Stock-take phrasing")
    r = await send(page, "add 15 kg pork to fridge")
    check("S17a stock-take", r, all_of=["pork"])
    if "confirm" in r.lower() or "?" in r:
        r = await send(page, "yes")
        check("S17b written", r, all_of=["pork"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S18 ── No silent write ───────────────────────────────────────────────────
    print(f"{sep}\nS18  No silent write — must confirm first")
    r = await send(page, "add 4 kg prawns to fridge")
    check("S18  confirm required", r, all_of=["prawn"], none_of=["added 4"])
    if "confirm" in r.lower() or "?" in r:
        r = await send(page, "yes")
        check("S18b then written", r, all_of=["prawn"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S19 ── pgvector memory recall ────────────────────────────────────────────
    print(f"{sep}\nS19  pgvector memory — ARIA recalls worker item history")
    r = await send(page, "what items have I been managing in the fridge?")
    check("S19  memory recall", r,
          any_of=["chicken", "lamb", "butter", "milk", "rice", "tuna",
                  "pork", "prawn", "fridge", "manage", "count", "worker"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # S20 ── Generic category (too vague) ─────────────────────────────────────
    print(f"{sep}\nS20  Too-vague item name")
    r = await send(page, "add some vegetables")
    check("S20  asks to clarify", r,
          all_of=["vegetable"],
          any_of=["which", "what", "specific", "tell me", "exactly", "?"])
    await asyncio.sleep(BETWEEN_PAUSE)

    # ── Summary ───────────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r[0] == PASS)
    total  = len(results)
    print(f"\n{'═'*68}")
    print(f"  RESULTS  {passed}/{total} passed")
    print(f"{'═'*68}")
    for icon, label, _ in results:
        print(f"  {icon}  {label}")
    print(f"{'═'*68}")
    if passed < total:
        print("\n  FAILURES:")
        for icon, label, resp in results:
            if icon == FAIL:
                print(f"    • {label}")
                print(f"      got: {resp}")
    print()


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, slow_mo=50)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        print("Setting up workspace…")
        await setup_workspace(page)

        await run(page)

        print("Keeping browser open 8 s so you can see the final state…")
        await asyncio.sleep(8)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
