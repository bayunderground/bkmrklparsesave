# extractor-spec.md — how to write a bkmrklparsesave rule

Audience: an LLM that receives this file plus a sample of page HTML and a
description of the wanted fields. Your output is pasted verbatim into the
RULES block of `extractor.user.js`.

## Task statement

Output **exactly one** `rules.push({...});` block and nothing else — no prose
before, no prose after (a short "Uncertainties" section after the block is the
only exception, see the end of this file).

## Rule object

```js
rules.push({
  name: 'products',                      // required; ^[a-z0-9][a-z0-9_-]{0,63}$; becomes data/products.md
  match: /example\.com\/shop\//,         // required; string (substring of URL), RegExp, or (url, doc) => boolean
  items: '.product-card',                // required; CSS selector, or (doc) => Element[]
  id: (el) => el.dataset.sku,            // optional; explicit ID (string); empty/missing -> server fallback chain
  filter: (item, el) => item.Price > 0,  // optional; return false to drop the item (runs after fields)
  fields: {                              // required; insertion order = output order; FIRST field = title + ID fallback
    Title:     '.title',
    Price:     (el) => parseFloat(el.querySelector('.price')?.textContent.replace(/[^\d.]/g, '')),
    Available: (el) => !el.querySelector('.sold-out'),
    URL:       { sel: 'a.title', attr: 'href', abs: true },
    Tags:      { sel: '.tag', all: true },
    Discount:  (el, item) => item.Price && item.Old ? item.Old - item.Price : null,
  },
});
```

Plain ES2019-style JavaScript. No imports, no network calls, no globals
beyond `document`/`location` and the three helpers. Rules run inside
try/catch: a thrown error becomes a visible warning, never a crash — but a
rule that throws has produced nothing for that field, so avoid it.

### `match` — when the rule applies

Exactly one of:

- **string**: treated as a substring test against the full URL.
- **RegExp**: tested against the full URL.
- **function** `(url, doc) => boolean`.

All rules whose `match` is true are candidates; if more than one matches, the
user picks in a list. Make `match` as narrow as the URL structure allows.

### `items` — which elements become items

- **string**: CSS selector, queried on the whole `document`.
- **function** `(doc) => Element[]`: custom discovery (e.g. walk a table).

Returns an array of elements. Empty array is fine (means "no items here").

### `fields` — what gets extracted

Insertion order is output order. **The first field is special**: its value
becomes the section title, and if no `id` is given it also becomes the
duplicate-detection identity. Put the most identifying field first.

A field value (the right-hand side) is exactly one of:

1. **string** — CSS selector scoped to the item element; the trimmed
   `textContent` of the first match.
2. **object** `{ sel, attr?, all?, abs? }`:
   - `sel` (required): CSS selector scoped to the item element.
   - `attr`: read this attribute instead of text.
   - `all`: collect **all** matches into an array instead of the first.
   - `abs`: resolve the value against the page base URL (for `href`/`src`).
3. **function** `(el, itemSoFar) => value` — custom logic. `el` is the item
   element; `itemSoFar` is the object of fields extracted **before** this one
   (in declaration order), so `Discount` can read `item.Price`.

**Allowed result values:** string, number, boolean, `null`/`undefined`, or a
flat array of strings/numbers/booleans.

**Missing means omitted, not an error:** `null`, `undefined`, `''`, `NaN`,
and an empty array all say "this field does not apply"; the field is simply
left out of the output.

**Warnings, not failures:** a selector that matches nothing (plain string
form) is "missing". A function that throws, or returns something not in the
allowed list (an object, a DOM node), is recorded as a warning shown in the
preview, and the field is treated as missing. Extraction continues.

### `id` — explicit duplicate identity

function `(el) => string`. Return the site's stable identifier (SKU,
`data-*` attribute, URL slug). Empty/missing/throwing -> the server falls
back to the first field, then to a content hash. Add `id` whenever a stable
one exists.

### `filter` — drop items

function `(item, el) => boolean`. Runs after all fields are resolved;
`return false` drops the item. `item` contains the already-extracted (and
therefore non-missing) fields. Throwing inside `filter` keeps the item and
shows a warning.

## Helpers

Only these three exist; they are the whole "API":

- `$text(el, sel)` → trimmed `textContent` of the first match, or `null`.
- `$attr(el, sel, name)` → attribute value of the first match, or `null`.
- `$all(el, sel)` → array of trimmed `textContent`, one entry per match.

They are conveniences inside field functions; the plain forms
(string / `{sel}` object) cover the same ground without them.

## Worked example 1 — simple list

Plain selectors, no computation:

```js
rules.push({
  name: 'events',
  match: 'example.com/events/',
  items: '.event-row',
  fields: {
    Title: 'h2',
    Date: { sel: 'time', attr: 'datetime' },
    Venue: '.venue',
    URL: { sel: 'a', attr: 'href', abs: true }
  }
});
```

## Worked example 2 — computed field + filter

Parsing and skipping happen in functions; the computed field reads earlier
fields:

```js
rules.push({
  name: 'auctions',
  match: /example\.com\/lots\//,
  items: '.lot',
  id: (el) => el.dataset.lotId,
  filter: (item) => item.Bid !== undefined && item.Bid > 0,
  fields: {
    Title: '.lot-title',
    Bid: (el) => {
      const t = el.querySelector('.current-bid');
      return t ? parseFloat(t.textContent.replace(/[^\d.]/g, '')) : null;
    },
    Ends: (el) => $attr(el, '.countdown', 'data-deadline'),
    Bidders: (el) => parseInt(($text(el, '.bid-count') || '0'), 10),
    High: (el, item) => item.Bid !== undefined && item.Bid > 1000
  }
});
```

`null` from `Bid` (no price node) means missing; `High` then stays missing
too. `filter` drops items whose bid is missing or zero.

## Worked example 3 — conditional/optional fields with explicit id

Only some cards have a badge or an old price; one field is an array:

```js
rules.push({
  name: 'books',
  match: (url) => /example\.com\/books\//.test(url),
  items: doc => Array.from(doc.querySelectorAll('li.book')),
  id: (el) => el.getAttribute('data-isbn'),
  filter: (item) => item.Price !== undefined,
  fields: {
    ISBN: (el) => el.getAttribute('data-isbn'),
    Title: '.title',
    Author: '.author',
    Price: (el) => {
      const t = el.querySelector('.price');
      if (!t) return undefined;
      const n = parseFloat(t.textContent.replace(/[^\d.]/g, ''));
      return isNaN(n) ? undefined : n;
    },
    OldPrice: (el) => {
      const t = el.querySelector('.price-old');
      return t ? parseFloat(t.textContent.replace(/[^\d.]/g, '')) : null;
    },
    OnSale: (el) => !!el.querySelector('.badge-sale'),
    Tags: { sel: '.tag', all: true },
    URL: { sel: 'a.title', attr: 'href', abs: true }
  }
});
```

`OldPrice` is omitted when the node is absent; `OnSale` still appears as
`false` (a boolean is a value, not a "missing").

## Hard rules

- Use **only** selectors that appear in the supplied HTML sample. Never
  invent class names, attributes, or structure "that sites like this usually
  have". If you cannot see it, you cannot select it.
- Prefer stable attributes (`data-*`, `id`, `href`, semantic tags) over
  positional selectors (`nth-child`, `:first-of-type`).
- The first field is the title **and** the duplicate-identity fallback: put
  the most identifying field first.
- Add `id` whenever the HTML exposes a stable identifier (SKU, ISBN,
  `data-*`, URL slug). Two different items that share their first-field value
  are treated as duplicates — an explicit `id` prevents that.
- No network calls, no timers, no mutation of the page. Read the DOM only.
- Result values must stay within: string, number, boolean, or flat array of
  those (plus `null`/`undefined`/`''`/`NaN` to mean "missing").

## Limitations of the environment (design your rule around these)

- The rule runs on the **live DOM at click time**: scroll or expand
  everything first; content that only loads later is not there.
- No network calls, no access to page JavaScript state.
- Iframes and shadow DOM are not reachable unless the sample HTML shows the
  content directly in the page.
- No pagination: one page per run. The user navigates and runs again.
- The sample HTML may be truncated; absence of a node in the sample is not
  proof the site never shows it (see "Uncertainties").

## Uncertainties section (required after the rule)

After your `rules.push({...});` block, add a short section:

```text
Uncertainties:
- <what the HTML sample could not confirm: missing states (out of stock,
  empty results), truncated markup, ambiguous selector matches, assumptions
  about URLs>
- <if crucial: "need a sample of <state/page> to decide">
```

Never paper over uncertainty with a guessed selector: list it instead, and
ask for more HTML rather than guessing.

## Prompt template (copy, fill in, attach the HTML)

```text
Attached: extractor-spec.md (the rule API) and a sample of the page HTML.

Page URL: https://example.com/shop/page/2
Task: Extract every product card. Fields: title, price as a number, whether it is
in stock, absolute product URL, and the tags. Skip items without a price.
Use the SKU from the data-sku attribute as the explicit id.
Rule name: products

Follow extractor-spec.md exactly. Output only one rules.push({...}) block, then a
short "Uncertainties" list. Do not invent selectors that are not in the HTML.
```

Variations: paste the HTML of *each* interesting state (normal, sold out,
empty page) when they differ, and say which URL each belongs to.
