"""Contact a landlord through a listing's contact form ("Nachricht").

Two steps, so nothing is sent by accident:

* ``apply_to_listing(id, message)`` fills the form, saves a screenshot and returns
  what would be sent, without submitting.
* ``apply_to_listing(id, message, confirm=True)`` fills it again and clicks "Abschicken".

Fields the account profile already prefills (name, e-mail, address, income, …) are
left alone unless overridden. Every send is logged; a listing is never contacted twice,
and a daily cap plus a minimum gap between sends keep volume low.
"""
from __future__ import annotations

import gzip
import json
import os
from datetime import date, datetime
from pathlib import Path

from .browser import _INSTALL_HINT, data_dir, launch, sync_playwright

EXPOSE_URL = "https://www.immobilienscout24.de/expose/{id}"

MAX_PER_DAY = int(os.environ.get("IMMOSCOUT_MAX_APPLICATIONS_PER_DAY", "20"))
MIN_INTERVAL_S = int(os.environ.get("IMMOSCOUT_MIN_SECONDS_BETWEEN_APPLICATIONS", "60"))

# Form fields that may be overridden, mapped to their allowed values (None = free text).
FIELDS: dict[str, list[str] | None] = {
    "salutation": ["DIVERS", "FEMALE", "MALE", "COMPANY"],
    "firstName": None,
    "lastName": None,
    "emailAddress": None,
    "phoneNumber": None,
    "street": None,
    "houseNumber": None,
    "postcode": None,
    "city": None,
    "moveInDateType": ["CONCRETE", "FLEXIBLE", "FROM_NOW"],
    "moveInDate": None,  # DD.MM.YYYY, only used with moveInDateType=CONCRETE
    "numberOfPersons": ["ONE_PERSON", "TWO_PERSON", "FAMILY", "BIG_GROUP"],
    "hasPets": ["TRUE", "FALSE"],
    "employmentRelationship": [
        "PUBLIC_EMPLOYEE", "WORKER", "SELF_EMPLOYED", "OFFICER", "TRAINEE", "STUDENT",
        "DOCTORAND", "HOUSEWIFE", "UNEMPLOYED", "RETIREE", "OTHER",
    ],
    "income": [
        "BELOW_500", "OVER_500_UPTO_1000", "OVER_1000_UPTO_1500", "OVER_1500_UPTO_2000",
        "OVER_2000_UPTO_3000", "OVER_3000_UPTO_4000", "OVER_4000_UPTO_5000", "OVER_5000",
    ],
    "applicationPackageCompleted": ["TRUE", "FALSE"],
    # Extended form (some listings ask these instead of / in addition to the above):
    "numberOfAdults": None,
    "numberOfKids": None,
    "employmentStatus": ["FIXED_TERM", "INDEFINITE_PERIOD"],
    "incomeAmount": None,  # monthly net household income in EUR, digits only
    "forCommercialPurposes": ["TRUE", "FALSE"],
    "rentArrears": ["TRUE", "FALSE"],
    "insolvencyProcess": ["TRUE", "FALSE"],
    "smoker": ["TRUE", "FALSE"],
    # Minimal form: "Stich hervor: Ja zur Kautionsbürgschaft+" checkbox.
    "depositGuarantee": ["TRUE", "FALSE"],
}

# Marketing opt-ins on the form that are always switched off.
_OPT_OUTS = ("#is24-checkbox-isRelocationOfferChecked-input",)


def _profile_path() -> Path:
    return Path(os.environ.get("IMMOSCOUT_PROFILE") or data_dir() / "profile.json")


def load_profile() -> dict:
    """Applicant defaults: {"message": str, "fields": {name: value}}; per-call values win."""
    path = _profile_path()
    if not path.exists():
        return {"message": "", "fields": {}}
    profile = json.loads(path.read_text(encoding="utf-8"))
    return {"message": profile.get("message", ""), "fields": profile.get("fields", {})}

_SUCCESS_HINTS = ("nachricht wurde", "erfolgreich", "gesendet", "verschickt", "vielen dank")


class ApplyError(Exception):
    pass


def _log_path() -> Path:
    return data_dir() / "applications.jsonl"


def load_log() -> list[dict]:
    path = _log_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_log(entry: dict) -> None:
    with _log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _check_limits(expose_id: str) -> None:
    sent = [e for e in load_log() if e.get("status") in ("sent", "sent_unverified")]
    if any(e["expose_id"] == expose_id for e in sent):
        raise ApplyError(f"Listing {expose_id} was already contacted.")
    today = date.today().isoformat()
    if sum(e["at"].startswith(today) for e in sent) >= MAX_PER_DAY:
        raise ApplyError(f"Daily cap of {MAX_PER_DAY} applications reached.")
    if sent:
        wait = MIN_INTERVAL_S - (datetime.now() - datetime.fromisoformat(sent[-1]["at"])).total_seconds()
        if wait > 0:
            raise ApplyError(f"Wait {int(wait) + 1}s before the next application.")


def _validate(fields: dict[str, str]) -> None:
    for key, value in fields.items():
        if key not in FIELDS:
            raise ApplyError(f"Unknown field {key!r}. Allowed: {', '.join(FIELDS)}")
        allowed = FIELDS[key]
        if allowed is not None and value not in allowed:
            raise ApplyError(f"{key} must be one of {allowed}, got {value!r}")


def _open_form(page, expose_id: str) -> None:
    page.goto(EXPOSE_URL.format(id=expose_id), wait_until="domcontentloaded", timeout=45000)
    button = page.locator('[data-testid="contact-button"]')
    # Shortly after load the page may bounce through a silent SSO refresh; wait until
    # the expose has shown the contact button on two consecutive checks.
    seen = 0
    for _ in range(25):
        page.wait_for_timeout(1000)
        if "roboter" in page.title().lower():
            raise ApplyError("ImmoScout shows a captcha. Run immoscout-login, solve it, and retry.")
        seen = seen + 1 if "/expose/" in page.url and button.count() else 0
        if seen >= 2 and _ >= 5:
            break
    else:
        raise ApplyError("This listing has no contact form (maybe deactivated or external).")
    button.first.click()
    # The first click can trigger a silent SSO round-trip that lands back on the expose.
    for attempt in range(30):
        page.wait_for_timeout(1000)
        if "/expose/" in page.url and page.locator('textarea[name="message"]').count():
            # The logged-out form asks for a privacy-policy tick and offers "Jetzt anmelden".
            if page.locator("#is24-checkbox-privacyPolicyAccepted-input").count():
                raise ApplyError("Not logged in to ImmoScout. Run immoscout-login, log in, and retry.")
            _wait_for_prefill(page)
            return
        if "/expose/" in page.url and attempt in (8, 16) and button.count():
            button.first.click()
    raise ApplyError(f"Contact form did not open (ended on {page.url}). Is the session logged in?")


def _wait_for_prefill(page) -> None:
    """The profile values load after the form renders; filling earlier gets overwritten."""
    for _ in range(20):
        # Forms differ per listing, so only require whichever profile fields exist.
        if page.evaluate(
            """() => ['firstName', 'emailAddress', 'numberOfPersons']
                .map(n => document.querySelector(`[name="${n}"]`))
                .filter(Boolean).every(e => e.value)"""
        ):
            break
        page.wait_for_timeout(500)
    page.wait_for_timeout(1000)


def _fill(page, message: str, fields: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Fill the form; returns its resulting values and the overrides it does not have."""
    form = page.locator("form").filter(has=page.locator('textarea[name="message"]')).first
    form.locator('textarea[name="message"]').fill(message)
    skipped = []
    for key, value in fields.items():
        if key == "moveInDate":
            continue
        el = form.locator(f'[name="{key}"]')
        if not el.count():
            skipped.append(key)
        elif el.is_disabled():
            # e.g. the e-mail is locked to the logged-in account.
            if el.input_value() != value:
                skipped.append(key)
        elif el.get_attribute("type") == "checkbox":
            if el.is_checked() != (value == "TRUE"):
                el.evaluate("e => e.click()")
        elif FIELDS[key] is None:
            el.fill(value)
        else:
            el.select_option(value)
    if "moveInDate" in fields:
        page.wait_for_timeout(300)
        date_input = form.locator("#moveInDate")
        if date_input.count():
            date_input.fill(fields["moveInDate"])
            # Typing opens a calendar popup that must be confirmed to commit the date.
            page.wait_for_timeout(300)
            confirm_btn = page.get_by_role("button", name="Bestätigen")
            if confirm_btn.count() and confirm_btn.first.is_visible():
                confirm_btn.first.click()
                page.wait_for_timeout(300)
        else:
            skipped.append("moveInDate")
    for selector in _OPT_OUTS:
        box = form.locator(selector)
        if box.count() and box.is_checked():
            # Styled toggles keep the real input off-screen; a DOM click still fires React's onChange.
            box.evaluate("e => e.click()")
            page.wait_for_timeout(200)
            if box.is_checked():
                raise ApplyError(f"Could not switch off opt-in {selector}.")
    values = form.evaluate(
        """f => Object.fromEntries([...f.querySelectorAll('input,select,textarea')]
            .filter(e => (e.name || e.id) && e.type !== 'hidden')
            .map(e => [e.name || e.id, e.type === 'checkbox' ? String(e.checked) : e.value]))"""
    )
    return values, skipped


def _describe(req) -> dict:
    """A request as JSON-safe data, without cookies; bodies may be gzip-compressed."""
    try:
        body = req.post_data_buffer
        if body and body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        text = body.decode("utf-8", "replace") if body else None
    except Exception as exc:  # recording must never break the send flow
        text = f"<unreadable body: {exc}>"
    headers = {k: v for k, v in req.headers.items() if k.lower() != "cookie"}
    return {"method": req.method, "url": req.url, "headers": headers, "body": text}


def _screenshot_form(page, form, path: Path) -> None:
    """The form lives in a fixed, internally scrolling side panel: grow the viewport so
    the whole form fits, then clip to the panel."""
    height = form.evaluate("f => f.scrollHeight") + 400
    page.set_viewport_size({"width": 1280, "height": max(900, min(height, 4000))})
    page.wait_for_timeout(500)
    form.evaluate("f => f.scrollIntoView({block: 'start'})")
    page.wait_for_timeout(300)
    box = form.evaluate(
        """f => { let p = f; while (p && p !== document.body && getComputedStyle(p).position !== 'fixed') p = p.parentElement;
                  const r = (p && p !== document.body ? p : f).getBoundingClientRect();
                  return {x: r.x, y: r.y, width: r.width, height: r.height}; }"""
    )
    page.screenshot(path=str(path), clip=box if box["width"] and box["height"] else None)


def apply(
    expose_id: str, message: str | None = None, fields: dict[str, str] | None = None, confirm: bool = False
) -> dict:
    """Fill (and with ``confirm`` submit) the contact form of a listing.

    ``message`` and ``fields`` default to the applicant profile (see ``load_profile``).
    """
    if sync_playwright is None:
        raise ApplyError(_INSTALL_HINT)
    expose_id = str(expose_id).strip()
    profile = load_profile()
    overrides = {k: str(v) for k, v in (fields or {}).items()}
    fields = {**{k: str(v) for k, v in profile["fields"].items()}, **overrides}
    message = message or profile["message"]
    if not message.strip():
        raise ApplyError("Message is empty and the profile has no default message.")
    _validate(fields)
    if confirm:
        _check_limits(expose_id)

    shots = data_dir() / "screenshots"
    shots.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    headless = os.environ.get("IMMOSCOUT_HEADLESS") == "1"

    with sync_playwright() as pw:
        ctx = launch(pw, headless=headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _open_form(page, expose_id)
            values, skipped = _fill(page, message, fields)
            form = page.locator("form").filter(has=page.locator('textarea[name="message"]')).first
            preview = shots / f"{expose_id}-{stamp}-preview.png"
            _screenshot_form(page, form, preview)
            result = {
                "expose_id": expose_id,
                "url": EXPOSE_URL.format(id=expose_id),
                "form_values": values,
                "empty_fields": [k for k, v in values.items() if k in FIELDS and v == ""],
                # Profile defaults missing from this form variant are expected; only
                # report explicit overrides the form does not have.
                "skipped_fields": [k for k in skipped if k in overrides],
                "screenshot": str(preview),
            }
            if not confirm:
                result["status"] = "preview"
                result["note"] = "Not sent. Call again with confirm=true to submit."
                return result

            # Passively record what the submit sends (listening only, never intercepting).
            captured: list[dict] = []
            page.on("request", lambda req: req.method != "GET" and captured.append(_describe(req)))
            form.get_by_role("button", name="Abschicken").click()
            page.wait_for_timeout(5000)
            capture_dir = data_dir() / "captures"
            capture_dir.mkdir(exist_ok=True)
            capture_file = capture_dir / f"{expose_id}-{stamp}.json"
            capture_file.write_text(json.dumps(captured, indent=1, ensure_ascii=False), encoding="utf-8")
            result["capture"] = str(capture_file)
            errors = page.locator('[aria-invalid="true"]').evaluate_all(
                "els => els.map(e => e.name || e.id)"
            )
            body = page.locator("body").inner_text().lower()
            sent_shot = shots / f"{expose_id}-{stamp}-sent.png"
            page.screenshot(path=str(sent_shot), full_page=False)
            result["screenshot"] = str(sent_shot)
            if errors:
                result["status"] = "failed"
                result["invalid_fields"] = errors
            elif not page.locator('textarea[name="message"]').count() or any(
                h in body for h in _SUCCESS_HINTS
            ):
                result["status"] = "sent"
            else:
                result["status"] = "sent_unverified"
                result["note"] = "Clicked Abschicken but saw no confirmation; check the screenshot."
        finally:
            ctx.close()

    _append_log({
        "at": datetime.now().isoformat(timespec="seconds"),
        "expose_id": expose_id,
        "status": result["status"],
        "message": message,
        "fields": fields,
        "screenshot": result["screenshot"],
    })
    return result
