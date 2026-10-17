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
