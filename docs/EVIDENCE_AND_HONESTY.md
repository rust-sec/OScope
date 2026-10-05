# Evidence and honesty: how OScope decides what it may say

OScope's value is explanation, and an explanation is only worth anything if it never claims more than the
evidence shows. These are the rules the code enforces (and the tests check).

## 1. A reading is either a measurement or an honest gap

Every piece of evidence is a `Reading` (`app/collectors/base.py`). It has a **status**, and a value is allowed
**only** when the status is *Available* (the constructor refuses anything else, so a number cannot be invented):

| Status | Meaning | Example |
|---|---|---|
| **Available** | It was measured. | CPU 42% |
| **Unavailable** | We tried and failed, or there was no data yet. | a counter name not found; "warming up: needs two measurements" |
| **Permission restricted** | Windows refused. | temperature needs administrator rights |
| **Not exposed by hardware** | The probe ran; this device reports no such sensor. | no fan speed, no battery on a desktop |
| **Not supported on this OS** | There is no collector for this platform. | everything Windows-specific in dev mode on Linux |

Every answer ends with *what it could not check*, using these words, and the Overview shows the state of every
source with the reason (*Details*).

## 2. How strongly a statement may speak

| Internal strength | What the user sees | Used for |
|---|---|---|
| Observed / Strong | **Measured** | a number read from the system |
| Correlation (Interpretation) | **Seen together** | two measurements at the same time, no claim about cause |
| Unverified | **Could not verify** | something OScope looked for and could not check |

A relationship rule may not claim more than *Seen together*: the `Rule` constructor rejects anything stronger.
A rule that needs a reading stays **silent** unless that reading is really available; it never fills a gap with a guess.

## 3. Words OScope never uses about *your* computer

`because`, `caused`, `causing`, `due to`, `culprit`, `responsible`, `blame`, `fix`, `result of`, `resulting from`.

`tests/test_analysis.py` runs every question, for every workload, over several synthetic machines (busy, idle,
no sensors, just started) and checks every sentence that comes out. General facts are stated as generalities
("Browsers normally run a separate process for each tab"), not as the explanation of this PC.

## 4. "High" means sustained

A single spike is not a problem. `app/analysis/trends.py` calls a metric high only if at least 60% of the recent
samples (at least 5 of them) were over its threshold. With less history OScope says so ("describing the current
moment rather than a trend") and treats the result as weaker.

## 5. The four layers

Each finding is: **what is happening** → **what is contributing** (evidence) → *(across findings)* **what you may
not have noticed** (relationships) → **what you could consider** (optional, worded as an option, never a button
that changes something). Layer 3 is shown only when something was actually found; otherwise OScope says
"No relationships between different measurements were found." instead of padding.

## 6. Workloads reorder, nothing more

Choosing *Gaming*, *Video editing*, *Photoshop/design*, *Music/audio* or *Rendering/3D* changes which findings and
relationships are listed first **within the same severity**. It never changes a threshold, hides a finding or turns a
measurement into a verdict. (Audio *dropouts* cannot be diagnosed without latency data OScope does not collect, so that
context only emphasises CPU spikes, background load and power.)

## 7. OScope's own thresholds

These are rough cut-offs for deciding what is *worth pointing out*. They are not hardware or Microsoft limits, and
the explanations quote what was measured rather than the cut-off. They live in `app/utils/constants.py`.

| Signal | Cut-off |
|---|---|
| CPU | high 80%, critical 95% |
| Memory | high 85%, critical 95%; commitment 90% of the limit |
| System drive | getting full 80%, low 90%, very low 95% |
| GPU (busiest engine) | 85% |
| Disk throughput | 100 MB/s combined (a rough "heavy" mark; says nothing about how busy the disk is) |
| ACPI temperature | 70 °C noteworthy, 85 °C warm; the zone is not necessarily the CPU |
| Free space for creative tools | below 15% or 20 GB |
| Startup programs | 10 or more |
| Browser processes | 10 or more |

## 8. What OScope does not measure

Per-process GPU (Windows reports the busiest engine, not a total); disk *busy time* and which program caused disk traffic;
fan speed; true CPU temperature without a vendor driver; services and scheduled tasks that start with Windows; which drive an
application uses for scratch space; audio latency; network. It says so instead of approximating.

## 9. Read-only

Nothing in `app/` deletes, moves or changes user files, ends processes, or runs commands that modify the system.
`tests/test_docs_and_safety.py` scans the code for write-type operations (outside OScope's own settings, history and report files)
and checks that the single PowerShell script it runs contains only `Get-` cmdlets.
