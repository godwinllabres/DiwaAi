# Philippine Profanity & Offensive-Language Lexicon (v0.1.0)

A structured, machine-readable lexicon of profanity, vulgarity, slurs, euphemisms, and
minced oaths across **10 language codes**: Tagalog/Filipino, Cebuano/Bisaya, Ilocano,
Hiligaynon/Ilonggo, Waray-Waray, Bikol Central, Kapampangan, Pangasinan, Chavacano
(Zamboangueño), and English as used in Philippine code-switching (Taglish/Bislish).

Built for **content moderation, NLP research, and linguistics reference** — the canonical
JSON is designed to become the data core of a reusable dependency library (see
`ARCHITECTURE-ADR.md`).

## Files

| File | Purpose |
|------|---------|
| `dist/ph_profanity_lexicon.json` | Canonical dataset: metadata, matching guidance, allowlist, 207 entries |
| `dist/ph_profanity_lexicon.csv` | Flat export of the same entries for spreadsheet review |
| `build.py` + `data_*.py` | Source of truth; regenerates and validates `dist/` |

## Coverage snapshot

| Lang | Language | Entries | Notes |
|------|----------|--------:|-------|
| tgl | Tagalog / Filipino | 112 | Deepest coverage incl. euphemism chains & swardspeak |
| ceb | Cebuano / Bisaya | 24 | yawa/pisti/atay expletive family, anatomy, giatay-type maledictions |
| hil | Hiligaynon / Ilonggo | 17 | Sourced largely from an Ilonggo wordlist (Gelhmo) |
| bcl | Bikol Central | 11 | Includes aswang-invoking maledictions |
| cbk | Chavacano (Zamboangueño) | 10 | Spanish-creole profanity (chinga, coño de vos nana) |
| pam | Kapampangan | 9 | Incl. archaic curses from colonial-era dictionaries |
| eng | English (PH code-switching) | 9 | Raw + localized forms (pakyu, shet handled under tgl) |
| ilo | Ilocano | 8 | ukinnam cluster, anatomy, takki |
| war | Waray-Waray | 4 | **Thin — needs native contributors** |
| pag | Pangasinan | 3 | **Thin — needs native contributors** |

**Totals:** 207 entries · 328 spelling/inflection variants · 29 allowlist (false-positive)
phrases · 22 slur entries (all typed) · 36 context-dependent entries flagged.

## Entry schema (data dictionary)

| Field | Type | Meaning |
|-------|------|---------|
| `id` | string | Stable key, `<lang>-<slug>` (e.g. `ilo-ukinnam`) |
| `term` | string | Canonical citation form |
| `lang` | enum | ISO 639-3 code of the primary language |
| `pos` | enum | `intj`, `n`, `adj`, `v`, `phrase`, `expr` |
| `register` | enum | `formal` · `standard` · `slang` · `euphemism` · `textspeak` · `archaic` — the formal↔informal axis |
| `severity` | int 1–4 | 1 mild · 2 moderate · 3 strong · 4 severe |
| `categories` | enum[] | e.g. `expletive`, `sexual-anatomy`, `family-honor`, `malediction`, `slur-ethnic`… |
| `is_slur` / `slur_type` | bool / enum | Identity-based slurs typed: ethnic, homophobic, ableist, sexist, classist |
| `context_dependent` | bool | True = innocent or in-group uses are common; **never auto-block** |
| `gloss_en` / `literal_en` | string | Meaning in English / literal translation |
| `variants` | string[] | Spellings, contractions, text-speak, key inflections (`tangina`, `pi`, `kakantutin`) |
| `euphemisms` | string[] | Minced/softened forms (`pucha`, `anak ng tokwa`, `coconana`) |
| `formal_equivalent` | string | Clinical/formal counterpart (`puke` → *ari ng babae / kaselanan*) |
| `etymology` / `notes` | string | Origin and usage/cultural notes |
| `false_friends` | string[] | Cross-language collisions (see below) |
| `also_used_in` | enum[] | Other PH languages where the term is current |
| `confidence` | enum | `high` · `medium` · `low` — compiler's attestation confidence |
| `sources` | id[] | Keys into the `sources` registry in the JSON |

### The formal ↔ informal axis

The `register` field carries the request's "formal and informal" distinction:

- **formal** — insults that survive in formal/literary prose: *mangmang*, *hangal*, *hunghang*
- **standard** — dictionary-attested everyday profanity: *putang ina*, *gago*, *yawa*
- **slang** — street/youth/swardspeak: *jakol*, *kupal*, *chaka*, *shunga*
- **euphemism** — minced oaths: *pucha*, *putragis*, *yati*, *sanamagan*, *bilat sa manok*
- **textspeak** — chat forms live mostly in `variants` (*pi*, *amp*, *tangna*, *bsii*)
- **archaic** — colonial-era curses kept for completeness (*antac nang inda mo*, 1732)

Additionally, `formal_equivalent` gives the polite/clinical counterpart of vulgar
anatomy/act terms (e.g. *kantot* → *pagtatalik*), so a writing assistant can suggest
register-appropriate replacements, not just censor.

## Cross-language false friends (the Scunthorpe layer)

These pairs make naive substring matching dangerous in Philippine text, and they are
first-class data (`false_friends` + `allowlist`):

- **puke** — English "vomit" vs Tagalog "vagina"
- **buto** — Tagalog "bone/seed" (innocent) vs Ilocano "penis"
- **libog** — Cebuano "confused" (innocent) vs Tagalog "lust"
- **burat** — Tagalog "penis" (strong) vs Hiligaynon "drunk" (mild)
- **boto** — "vote" everywhere vs Bikol "penis"
- **agi** — Cebuano/Waray "to pass by" vs Hiligaynon gay slur
- **atay** — "liver" (food) vs Cebuano expletive
- **supot** — "bag" vs "uncircumcised" taunt
- **conyo/coño** — Manila "posh kid" (mild) vs Chavacano/Spanish "cunt" (strong)
- substring traps: *putahe, disputa, reputasyon, magaganda, kagagawan, leche flan*

## Methodology & sources

Compiled 2026-07-10 by desk research over public linguistic references, regional
language blogs, and compiler (native-level) knowledge of Philippine usage; validated
programmatically by `build.py` (unique IDs, enum checks, slur-flag consistency,
duplicate detection). Primary sources, all recorded per-entry:

- [Wikipedia — Tagalog profanity](https://en.wikipedia.org/wiki/Tagalog_profanity)
- [Spot.ph — Meaning of Popular Filipino Bad Words](https://www.spot.ph/newsfeatures/the-latest-news-features/87051/meaning-of-filipino-bad-words-like-leche-gago-and-yawa-a833-20210812)
- [Lingopie — 30+ Tagalog Swear Words](https://lingopie.com/blog/tagalog-swear-words/)
- [TalkBisaya — Bisaya Bad Words](https://www.talkbisaya.com/bisaya-bad-words) · [Bisdak Words](https://bisdakwords.com/common-bisaya-swear-words/)
- [Gelhmo — Hiligaynon Bad/Curse Words](https://gelhmo.com/hiligaynon-bad-curse-words-to-know/)
- [The Aninipot — How to Swear in Bikol](https://theaninipot.wordpress.com/2016/01/16/how-to-swear-in-bikol-6-most-commonly-used-bikol-curse-words-and-how-to-use-them-correctly/) · [J. Cordial — Bikol angry register](https://medium.com/@jeremiahcordial/linguistic-analysis-on-the-angry-speech-register-of-bikol-and-why-exoticizing-it-is-bad-f2bb92f61a8d)
- [SunStar — Tantingco: Are Kapampangans Foul-mouthed?](https://www.sunstar.com.ph/more-articles/tantingco-are-kapampangans-foul-mouthed) · [Taga Pampanga Ku](https://m.facebook.com/tagapampangakuofficial/photos/904183026423877/)
- [Bien Chabacano — The F Word in Chabacano](https://bienchabacano.blogspot.com/2010/09/f-word-in-chabacano.html)
- [WarayBlogger — What is Iroy?](https://www.warayblogger.com/2012/01/waray-tutorial-what-is-iroy.html)
- [Glosbe Pangasinan dictionary](https://glosbe.com/pag/en/ambagel) · [Kaikki Cebuano dictionary](https://kaikki.org/dictionary/Cebuano/meaning/k/ka/kayata.html) · [Quora — kayata/kulira](https://www.quora.com/What-s-the-difference-between-the-Cebuano-swear-words-kayata-and-kulira-kulera-Are-they-used-in-the-same-way-What-does-the-expression-puwa-og-ulok-mean)

Related datasets worth knowing (prior art, not ingested):
[jromest/filipino-badwords-list](https://github.com/jromest/filipino-badwords-list) (npm),
[dsojevic/profanity-list](https://github.com/dsojevic/profanity-list) (schema inspiration),
[mginoben/tagalog-profanity-dataset](https://huggingface.co/datasets/mginoben/tagalog-profanity-dataset)
and [hate_speech_filipino](https://huggingface.co/datasets/legacy-datasets/hate_speech_filipino) (HuggingFace).

## Limitations

1. **Waray, Pangasinan, and Kapampangan coverage is thin** and partly low-confidence —
   entries carry `confidence` flags; recruit native reviewers before production use.
2. `model`-sourced entries reflect compiler knowledge and need native-speaker sign-off.
3. Severity is context-collapsed to a single integer; Philippine profanity is famously
   tone-dependent ("tone and relationship matter more than the word").
4. Bikol's *tamanggot/rapsak* angry register (neutral words swapped for angry-register
   forms like *turóg → tuspók*) is documented in `notes`/README only — it is a register
   phenomenon, not profanity, and should not be filtered.
5. No frequency data yet; consider deriving weights from the HuggingFace corpora above.

## Ethics & intended use

This lexicon exists to **detect and moderate** abusive language, support linguistic
research, and help writing tools suggest register-appropriate alternatives. It is not a
tool for generating harassment. Identity terms (*bakla, bayot, agi, badi, tomboy*) are
flagged `context_dependent` because they are everyday self-identifiers; auto-blocking
them censors the communities moderation is meant to protect.

## Regenerating

```bash
python3 build.py   # validates and rewrites dist/
```
