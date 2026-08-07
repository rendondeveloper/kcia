# kcia — Plan de optimización de contexto (handoff para agente ejecutor)

> **Documento de especificación ejecutable.** Está escrito para que un agente lo implemente
> de principio a fin sin decisiones de diseño propias. Contiene rutas de archivo exactas,
> firmas de función, esquemas YAML, criterios de aceptación medibles y una lista explícita
> de cosas que **no** debe hacer.
>
> **Repo:** `rendondeveloper/kcia`, rama `master`.
> **Estado del repo al escribir esto:** commit `110af3b`, 46 tests en verde.
> **Prerrequisito:** `.venv/bin/pytest` pasa antes de empezar. Si no pasa, para y reporta.

---

## 0. Objetivo y principio rector

**Objetivo:** reducir los tokens de entrada por tarea sin perder calidad de la guía que
recibe el modelo, moviendo trabajo determinista de la ventana de contexto a Python.

**Principio rector — aplícalo ante cualquier duda:**

> Python resuelve lo que tiene **respuesta única y verificable** (qué archivos existen, qué
> comando corre en qué directorio, si un test pasó). El modelo decide lo que **depende del
> problema concreto** (qué es relevante para esta tarea, cómo diseñar la solución).

Si una optimización te obliga a adivinar qué necesita el modelo para una tarea específica,
**no la hagas**: estás cruzando la línea y vas a degradar el resultado.

### Línea base medida (commit `110af3b`)

Sobre `tests/fixtures/repos/melos_mono` con un profile activo (`backend-dart`):

| Métrica | Valor |
|---|---|
| Prompt medio por wave | 11 854 chars (~2 963 tokens) |
| Bloque fijo (guardrails + bundles) por wave | 10 631 chars (~2 657 tokens) |
| Porcentaje fijo de cada prompt | **89 %** |
| Total entrada por tarea (5 waves) | ~14 818 tokens |
| De los cuales son el bloque fijo repetido | ~13 288 tokens |

Desglose del bloque fijo: guardrails 6 212 chars, bundles de profiles 4 419 chars.

**Meta de este plan:** reducir el total de entrada por tarea **≥ 35 %** en ese mismo
fixture y escenario, sin que ninguna wave pierda las referencias que sí necesita.

---

## 1. Restricciones (no negociables)

1. **`control-plane/` no contiene código Python.** Todo lo configurable de este plan se
   expresa en YAML dentro de `control-plane/`. Si te ves añadiendo una lista de nombres de
   archivo hardcodeada en `cli/`, el diseño está mal.
2. **Compatibilidad hacia atrás de los packs existentes.** Un `profile.yaml` que hoy valida
   debe seguir validando sin cambios. No subas `schema_version` de profile ni de pack.
3. **No toques el contrato de los providers.** `providers/` no se modifica en este plan.
4. **No cambies el orden de secciones del prompt** definido en `waves/prompts.py`. Este plan
   quita contenido de secciones existentes; no reordena ni añade secciones nuevas salvo
   donde se indica explícitamente (Fase 4).
5. **Cada fase termina con `.venv/bin/pytest` en verde.** No avances con tests rojos.
6. **Un commit por fase**, con el mensaje indicado en cada una.

### Fuera de alcance — NO lo hagas

- No implementes `edit_scope`, `doctor`, `sync`, `ask`, `branch`, `auth` ni `mcp`.
- No añadas precios ni cálculo de costo.
- No implementes resume de sesión ni prompt caching.
- No reescribas `providers/claude.py` ni `providers/cursor.py`.
- No cambies el formato de `.ai/manifest.yaml`.
- No refactorices código que no toque este plan.

---

## FASE 0 — Instrumentación y línea base (obligatoria, va primero)

Sin medición no hay optimización demostrable. Esta fase no cambia comportamiento.

### Entregables

**`cli/src/kcia/waves/budget.py`** (nuevo)

```python
"""Token estimation and prompt composition accounting."""

CHARS_PER_TOKEN = 4

def estimate_tokens(text: str) -> int:
    """Heurística de 4 chars/token. Es una estimación, no un contador exacto;
    se usa para decisiones de truncado y para métricas comparativas."""

@dataclass(frozen=True)
class SectionStat:
    name: str          # "guardrails" | "profile:backend-dart" | "task-context" | ...
    chars: int
    tokens: int
    dropped: bool = False   # True si el presupuesto lo eliminó (Fase 2)

@dataclass
class PromptStats:
    sections: list[SectionStat]

    @property
    def total_tokens(self) -> int: ...
    @property
    def dropped_tokens(self) -> int: ...
    def as_dict(self) -> dict: ...
```

**`cli/src/kcia/waves/prompts.py`** — refactor mínimo, sin cambio de comportamiento:

- Añade `build_prompt_with_stats(wave, session, *, validation_error=None) -> tuple[str, PromptStats]`.
- `build_prompt(...)` pasa a ser un wrapper que devuelve solo el string. **La firma pública
  actual no cambia**, para no romper `waves/runner.py` ni los tests existentes.
- Internamente, cada bloque que hoy se hace `sections.append(...)` debe registrar un
  `SectionStat`. Nombres de sección exactos, en este orden:
  `role`, `guardrails`, `project-context`, `profile:<id>` (uno por profile activo),
  `task-context`, `ticket-context`, `plan-context`, `validation-error`, `injections`,
  `wave-instruction`, `output-format`.

**`tests/test_budget.py`** (nuevo) — unitarios de `estimate_tokens` y `PromptStats`.

**`tests/test_prompt_composition.py`** (nuevo) — el **test de regresión de línea base**:

```python
def test_baseline_prompt_size(melos_session):
    """Ancla la línea base. Este test DEBE actualizarse conscientemente en cada fase
    que reduzca tokens, y su valor solo puede BAJAR."""
    _, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    assert stats.total_tokens <= 3100   # línea base medida: ~2963
```

Crea el fixture `melos_session` en `tests/conftest.py`: copia
`tests/fixtures/repos/melos_mono` a un `tmp_path`, corre `kcia init --yes` sobre él y
`Session.create(...)` en modo prompt. Reutilízalo en todas las fases.

### Criterios de aceptación

1. `.venv/bin/pytest` en verde con los tests nuevos.
2. `build_prompt` produce **exactamente el mismo string** que antes de la fase. Verifícalo
   con un test que compare contra un fixture congelado en
   `tests/fixtures/prompts/understanding-baseline.md`.
3. `stats.total_tokens` para `understanding` en `melos_mono` está entre 2 900 y 3 000.

**Commit:** `feat(budget): add prompt token accounting and baseline regression test`

---

## FASE 1 — Filtrado de referencias por wave (el ahorro grande)

Hoy `waves/prompts.py` inyecta **todas** las referencias de **todos** los profiles activos
en **todas** las waves. `understanding` no necesita la guía de arquitectura ni las reglas de
mappers.

### 1.1 Modelo de datos

**Referencias con tags.** En `profile.yaml`, `references` admite **dos formas**, y la forma
string actual sigue siendo válida:

```yaml
references:
  - references/coding.md                              # forma string: tag = stem del archivo
  - { path: references/architecture.md, tags: [architecture, design] }
```

Regla de derivación del tag por defecto: si es string, el tag es el **stem del archivo**
(`references/coding.md` → `coding`). Una referencia siempre tiene al menos un tag.

**Waves declaran qué tags quieren.** En `control-plane/waves/waves.yaml`, cada wave gana un
campo opcional `reference_tags`:

```yaml
  - id: understanding
    order: 1
    agent: planner
    reference_tags: [coding, monorepo]          # NO architecture, NO validation
    # ...
  - id: analysis
    reference_tags: [coding, architecture, testing, validation, monorepo]
  - id: documentation-init
    reference_tags: []                          # ninguna referencia de profile
  - id: implementation
    reference_tags: [coding, testing, validation, api, data, web, accessibility]
  - id: documentation-final
    reference_tags: []
```

Semántica exacta:

- Campo **ausente** → se inyectan **todas** las referencias (comportamiento actual;
  compatibilidad hacia atrás para waves.yaml de terceros).
- Lista **vacía** → no se inyecta ninguna referencia de profile (las reglas booleanas sí).
- Lista con tags → se inyectan solo las referencias con **al menos un** tag coincidente.

### 1.2 Cambios de código

**`cli/src/kcia/profiles/schema.py`**

```python
class ReferenceSpec(BaseModel):
    path: str
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def default_tag_from_stem(self) -> "ReferenceSpec":
        """Sin tags explícitos, el tag es el stem del archivo."""

# En ProfileSpec:
references: list[str | ReferenceSpec] = Field(default_factory=list)

@field_validator("references", mode="after")
def normalize_references(cls, value): 
    """Normaliza toda entrada string a ReferenceSpec. El resto del código solo
    ve ReferenceSpec, nunca str."""
```

**NO subas `SUPPORTED_PROFILE_SCHEMA`.** Sigue en 2: la forma string sigue siendo válida, el
cambio es aditivo.

**`cli/src/kcia/profiles/inheritance.py`**

`ResolvedProfile.references` pasa de `list[tuple[str, Path]]` a
`list[ReferenceEntry]` donde:

```python
@dataclass(frozen=True)
class ReferenceEntry:
    profile_id: str      # profile que la declaró (para atribución)
    path: Path           # ruta absoluta ya resuelta
    tags: tuple[str, ...]
```

Las reglas de composición **no cambian**: concatenación padre-primero, sin dedup.
Actualiza todos los consumidores: `profiles/bundle.py`, `waves/prompts.py`,
`commands/profile.py` (el `show` que imprime `[profile] archivo.md`).

**`cli/src/kcia/waves/definitions.py`**

```python
@dataclass(frozen=True)
class WaveDefinition:
    # ...campos actuales...
    reference_tags: tuple[str, ...] | None = None   # None = todas
```

Parsea con `raw.get("reference_tags")`, distinguiendo ausente (`None`) de lista vacía
(`()`). **Cuidado: `raw.get("reference_tags", [])` está mal**, borra la distinción.

**`cli/src/kcia/waves/prompts.py`**

```python
def _references_for_wave(
    resolved: ResolvedProfile, wave: WaveDefinition
) -> list[ReferenceEntry]:
    """Filtra por tags. `wave.reference_tags is None` => todas."""
```

Úsalo en el bucle de profiles activos. Registra en `PromptStats` una `SectionStat` por
profile con los chars efectivamente inyectados.

### 1.3 Contenido del control plane

Añade tags explícitos a las referencias de los cuatro profiles builtin. Tags a usar
(vocabulario cerrado, no inventes otros):

`coding`, `testing`, `validation`, `architecture`, `monorepo`, `api`, `data`, `web`,
`accessibility`.

Mapeo obligatorio:

| Archivo | Tags |
|---|---|
| `_dart-core/references/coding.md` | `[coding]` |
| `_dart-core/references/testing.md` | `[testing]` |
| `_dart-core/references/validation.md` | `[validation]` |
| `_dart-core/references/architecture.md` | `[architecture]` |
| `_dart-core/references/monorepo.md` | `[monorepo]` |
| `backend-dart/references/api.md` | `[api, architecture]` |
| `backend-dart/references/data.md` | `[data, architecture]` |
| `mobile-flutter/references/accessibility.md` | `[accessibility, coding]` |
| `web-flutter/references/web.md` | `[web, coding]` |

Sube `control-plane/VERSION` a `1.1.0` (cambio de datos, aditivo).

### 1.4 Tests

**`tests/test_prompt_composition.py`** — añade:

- `understanding` **no** contiene el texto de `architecture.md`.
- `analysis` **sí** lo contiene.
- `documentation-init` no contiene ninguna referencia de profile, pero **sí** contiene las
  reglas booleanas.
- Una wave sin `reference_tags` en un `waves.yaml` de fixture inyecta todas.
- Un `profile.yaml` con `references` en forma string (fixture `nodepack`) sigue cargando y
  su tag derivado es el stem.

**`tests/test_profiles.py`** — añade que `ReferenceSpec` normaliza ambas formas y que
`resolve_inheritance` propaga tags a través de la herencia.

### Criterios de aceptación

1. `pytest` en verde. Los tests existentes de `nodepack` (forma string) pasan sin tocarlos.
2. `stats.total_tokens` de `understanding` en `melos_mono` **baja al menos 20 %** respecto a
   la línea base de Fase 0. Actualiza el umbral del test de regresión al valor nuevo.
3. `kcia profile show backend-dart` sigue listando sus referencias con atribución.
4. Los bundles de `.ai/generated/profiles/<id>/references.md` siguen conteniendo **todas**
   las referencias — el filtrado es solo para el prompt, no para el bundle en disco.

**Commit:** `feat(prompts): filter profile references by wave via tags`

---

## FASE 2 — Presupuesto de contexto y truncado por prioridad

### 2.1 Configuración

En `control-plane/waves/waves.yaml`, a nivel raíz:

```yaml
schema_version: 1
budget:
  max_prompt_tokens: 120000
  drop_order: [architecture, monorepo, data, api, web, accessibility, testing, validation, coding]
waves:
  # ...
```

`drop_order` es el orden en que se **descartan** tags cuando el prompt excede el
presupuesto: el primero cae primero. `coding` es el último y en la práctica no debería caer
nunca.

Override por usuario en `~/.config/kcia/config.yaml`:

```yaml
preferences:
  max_prompt_tokens: 60000
```

Precedencia: preferencia de usuario > `waves.yaml` > default 120 000.

### 2.2 Código

**`cli/src/kcia/waves/budget.py`** — añade:

```python
def apply_budget(
    entries: list[ReferenceEntry],
    *,
    fixed_tokens: int,
    max_tokens: int,
    drop_order: list[str],
) -> tuple[list[ReferenceEntry], list[ReferenceEntry]]:
    """Devuelve (conservadas, descartadas).

    Algoritmo, exacto:
      1. Si fixed_tokens + suma(entries) <= max_tokens: devuelve (entries, []).
      2. Recorre drop_order. Por cada tag, descarta TODAS las entradas cuyo
         conjunto de tags contenga ese tag y que sigan presentes.
      3. Tras cada tag descartado, recomprueba el total. Para en cuanto quepa.
      4. Si tras agotar drop_order sigue sin caber, devuelve lo que queda; NO
         trunca a mitad de archivo y NO lanza.
    """
```

`fixed_tokens` = role + guardrails + contextos + instrucción de wave. Los guardrails
**nunca** se truncan.

**`cli/src/kcia/waves/prompts.py`** — aplica `apply_budget` tras el filtrado por tags.
Cada entrada descartada produce un `SectionStat(dropped=True)`.

**Visibilidad obligatoria.** Cuando algo se descarta:

- Se añade al final del prompt un bloque literal:
  ```
  ## Context budget
  The following guidance was omitted to fit the context budget: <archivos>.
  Ask for it explicitly if you need it.
  ```
- Se persiste en la sesión: `waves.<id>.dropped_references: [...]`.
- `kcia wave run` imprime `warning: dropped N reference(s) to fit the context budget`.

### 2.3 Tests

- Presupuesto holgado → no descarta nada.
- Presupuesto minúsculo (ej. 500) → descarta siguiendo `drop_order` exactamente, y `coding`
  es lo último en caer.
- Lo descartado aparece en el bloque `## Context budget` y en `session.json`.
- Los guardrails siguen presentes aunque el presupuesto sea absurdo.

### Criterios de aceptación

1. `pytest` en verde.
2. Un test compone un prompt con `max_prompt_tokens: 500` y verifica el orden exacto de
   descarte.
3. Con el default de 120 000, el fixture `melos_mono` **no descarta nada** (no debe activarse
   en uso normal).

**Commit:** `feat(prompts): enforce a context budget with priority-ordered dropping`

---

## FASE 3 — Acotar profiles activos al alcance real

Hoy `waves/runner.py` pasa `touched=[session.repo_root]` y `prompts.py` cae en
`resolve_for_cwd(repo_root, ...)`, que devuelve todos los profiles del monorepo. Si la tarea
vive en `packages/api`, sobran los bundles de `mobile-flutter` y `web-flutter`.

### 3.1 Alcance de la tarea

`kcia task init` gana una opción:

```
kcia task init "<texto>" [--scope <path>]...
```

- `--scope` es repetible; cada valor es un path relativo al repo root.
- Se persiste en `session.json` como `task.scope: ["packages/api"]`.
- Sin `--scope`, `task.scope` es `[]` y el comportamiento es el actual (todos los profiles).

**No infieras el scope automáticamente.** Adivinar de qué trata la tarea antes de que el
planner la lea es exactamente la línea que el principio rector prohíbe cruzar.

### 3.2 Código

**`cli/src/kcia/waves/session.py`** — `Session.create` acepta y persiste `scope`.

**`cli/src/kcia/waves/prompts.py`** — resolución de profiles activos, en este orden:

1. `session.data["active_profiles"]` si no está vacío.
2. Si `task.scope` no está vacío: `resolve_for_task([repo_root / s for s in scope], manifest, repo_root)`.
3. Si no: `resolve_for_cwd(repo_root, manifest, repo_root)` (actual).

**`cli/src/kcia/commands/task.py`** — la opción `--scope`, y `task show` imprime el scope.

### 3.3 Tests

- `task init --scope packages/api` en `melos_mono` → el prompt contiene el bundle de
  `backend-dart` y **no** los de `mobile-flutter` ni `web-flutter`.
- Sin `--scope` → contiene los tres (comportamiento actual intacto).
- `--scope` con un path inexistente → error accionable, exit 1, antes de crear la sesión.

### Criterios de aceptación

1. `pytest` en verde.
2. En `melos_mono` con `--scope packages/api`, el total de entrada por tarea baja **≥ 25 %**
   respecto a la misma tarea sin scope.

**Commit:** `feat(task): scope a task to paths so only relevant profiles are injected`

---

## FASE 4 — Mapa del repositorio precomputado

El sumidero más caro no es el prompt, son las tool calls del modelo explorando el repo.
Python ya tiene esa información en el manifest y no la está usando.

### 4.1 Contenido

Sección nueva del prompt, **inmediatamente después de `project-context`**, con nombre de
sección `repo-map`. Generada por Python, nunca por el modelo:

```markdown
## Repository map

Layout: monorepo. Detected 4 packages.

| Path | Profile | Test | Lint |
|---|---|---|---|
| packages/api | backend-dart | `dart test` | `dart analyze` |
| packages/app_mobile | mobile-flutter | `flutter test` | `flutter analyze` |
| packages/app_web | web-flutter | `flutter test` | `flutter analyze` |
| packages/shared | backend-dart | `dart test` | `dart analyze` |

Shared packages consumed by others: packages/shared (used by backend-dart, mobile-flutter).
```

Fuentes, todas deterministas: `manifest.profiles[].roots`, los comandos resueltos por
profile (`profile → command_overrides → manifest overrides`), y `manifest.dependencies`.

**No incluyas listados de archivos.** El mapa es de paquetes y comandos, no un `find`.

### 4.2 Código

**`cli/src/kcia/waves/repomap.py`** (nuevo)

```python
def build_repo_map(manifest: Manifest, registry: ProfileRegistry, repo_root: Path) -> str:
    """Markdown determinista. Cadena vacía si el manifest no tiene profiles."""
```

Reutiliza `_resolve_commands` de `waves/validation.py`; **extráelo** a un módulo compartido
(`profiles/commands.py`) en vez de duplicarlo, y actualiza el import en `validation.py`.

**`cli/src/kcia/waves/prompts.py`** — inserta la sección y su `SectionStat`.

### 4.3 Tests

- `melos_mono` → el mapa lista los 4 paquetes con su profile y comandos correctos, y
  `packages/api` aparece con `dart test`, **no** `flutter test`.
- Repo de un solo paquete → `Layout: single`, una fila.
- Manifest sin profiles → cadena vacía, y el prompt no contiene el encabezado.

### Criterios de aceptación

1. `pytest` en verde.
2. El mapa añade **< 400 tokens** en `melos_mono`. Si se pasa, recorta columnas, no lo hagas
   opcional.

**Commit:** `feat(prompts): inject a precomputed repository map`

---

## FASE 5 — Verificación del ahorro y documentación

### Entregables

**`tests/test_optimization_budget.py`** (nuevo) — el test que cierra el plan:

```python
def test_total_task_input_is_below_target(melos_session):
    """Suma de los 5 prompts de una tarea. Línea base commit 110af3b: ~14818 tokens."""
    total = sum(
        build_prompt_with_stats(w, melos_session)[1].total_tokens
        for w in load_waves()
    )
    assert total <= 9600     # >= 35% por debajo de 14818
```

**`README.md`** — en la sección `How it works`, actualiza:

- El apartado de composición del prompt: menciona el filtrado por tags y el presupuesto.
- **Elimina** de `Known gaps` los puntos ya resueltos: «No context budget» y el de acotar
  profiles. Deja los que sigan siendo ciertos (`edit_scope`, workflows no inyectados,
  sesiones no resumidas, runner sin lista de archivos tocados).
- Añade una tabla corta con la mejora medida: tokens por tarea antes y después.

**`CHANGELOG.md`** — entrada en `Unreleased` describiendo las cinco fases.

**`control-plane/VERSION`** — verifica que quedó en `1.1.0`.

### Criterios de aceptación

1. `pytest` en verde, incluido el test de ahorro total.
2. El README no promete nada que el código no haga. Verifícalo leyendo el código, no este
   plan.
3. `kcia init --yes` sobre `melos_mono` sigue siendo idempotente
   (`git status` limpio en la segunda corrida).
4. `kcia wave list`, `kcia task show`, `kcia profile show` y `kcia profile detect` siguen
   funcionando sin cambios de formato no documentados.

**Commit:** `docs: document context optimization and record the measured saving`

---

## 6. Orden de trabajo y dependencias

```
Fase 0 ──> Fase 1 ──> Fase 2 ──> Fase 5
       └─> Fase 3 ──────────────┘
       └─> Fase 4 ──────────────┘
```

- **Fase 0 va primero, sin excepción.** Es la que hace verificable todo lo demás.
- Fase 2 depende de Fase 1 (necesita los tags).
- Fases 3 y 4 son independientes entre sí y de la 1; pueden ir en cualquier orden después de
  la 0.
- Fase 5 va al final.

**Si hay que recortar alcance, recorta en este orden:** Fase 4 → Fase 3 → Fase 2. **Nunca**
recortes la Fase 0 ni la Fase 1: la 0 es la que demuestra el resultado y la 1 es el 80 % del
ahorro.

---

## 7. Cómo reproducir la medición

Ejecuta esto antes de empezar y después de cada fase; anota el resultado en el commit:

```bash
cd /ruta/a/un/melos_mono/inicializado
python - <<'EOF'
from pathlib import Path
from kcia.waves.session import Session
from kcia.waves.definitions import load_waves
from kcia.waves.prompts import build_prompt
s = Session.load(Path("."))
total = 0
for w in load_waves():
    p = build_prompt(w, s)
    total += len(p) // 4
    print(f"{w.id:22} {len(p):8} chars  ~{len(p)//4:6} tokens")
print(f"{'TOTAL':22} {'':8}        ~{total:6} tokens")
EOF
```

Preparación del repo de prueba:

```bash
cp -r tests/fixtures/repos/melos_mono /tmp/mono && cd /tmp/mono
git init -q .
kcia init --yes
kcia task init "arregla el overflow"
```

---

## 8. Riesgos y cómo reaccionar

| Riesgo | Señal | Qué hacer |
|---|---|---|
| El filtrado por tags deja a una wave sin guía que sí necesitaba | El modelo pregunta por convenciones que están en una referencia filtrada | Ajusta `reference_tags` en `waves.yaml` — **es dato, no código**. No añadas lógica. |
| Los tags derivados del stem chocan entre profiles | Dos archivos `coding.md` de profiles distintos comparten tag | Es correcto y deseado: ambos se inyectan o ninguno. No lo "arregles". |
| `ReferenceEntry` rompe consumidores que esperaban tuplas | Errores de desempaquetado en `bundle.py` o `profile.py` | Actualiza los consumidores; no añadas compatibilidad dual. |
| El presupuesto se activa en uso normal | Warning de descarte en `melos_mono` con el default | El default está mal calibrado o algo infla el prompt. Investiga antes de subir el default. |
| El mapa del repo crece con el número de paquetes | > 400 tokens en un monorepo grande | Recorta columnas o agrupa por profile. No lo hagas opcional ni lo muevas al modelo. |
