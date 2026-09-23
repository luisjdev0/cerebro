# cerebro-docs (`docs_*`)

Documentos Markdown **completos**, sin truncar ni destilar: guías,
especificaciones, actas, runbooks, cualquier texto que deba conservarse
íntegro. No cuentan como memoria formal — a diferencia de `memory_remember`,
acá nunca resumís ni recortás el contenido.

## Herramientas

| Herramienta | Úsala para |
|---|---|
| `docs_search` | Buscar documentos por texto (título + contenido) ante una referencia imprecisa ("la guía de despliegue"). Nunca incluye archivados. |
| `docs_get` | Leer un documento completo cuando ya sabés su ruta exacta `/{categoria}/{slug}`. Funciona igual para archivados y categorías ocultas. |
| `docs_list` | Explorar qué documentos existen, en una categoría o en todo el repositorio. Nunca incluye archivados. |
| `docs_list_archived` | Enumerar documentos archivados (para desarchivar o decidir si borrar definitivamente). |
| `docs_categories` | Ver las categorías existentes y sus descripciones. Llamala antes de `docs_save` si no sabés cuál usar. Nunca incluye categorías ocultas. |
| `docs_create_category` | Crear una categoría nueva SOLO si ninguna existente encaja. Acepta `hidden`/`locked` (ver abajo). |
| `docs_save` | Guardar un documento Markdown **nuevo**, completo, sin destilar. Falla si el slug ya existe en esa categoría — nunca sobrescribe en silencio. |
| `docs_update` | Reemplazo COMPLETO de un documento existente (puede moverlo de categoría). Archiva un snapshot del contenido anterior antes de reemplazar. |
| `docs_patch_section` | Parche PARCIAL por sección (heading → siguiente heading de igual o mayor nivel). Operaciones: `replace`, `append`, `insert_after`, `insert_before`, `delete`. El `heading` debe matchear (con o sin el prefijo `#`) y ser único, o falla. |
| `docs_archive` / `docs_unarchive` | Sacar un documento de circulación sin perderlo (soft-delete reversible) / revertirlo. Preferí esto sobre `docs_delete` casi siempre. |
| `docs_history` | Leer el historial de versiones de un documento (contenido completo por versión). Solo lectura, sin restore automático — si necesitás recuperar una versión vieja, copiá su contenido y guardalo con `docs_update`. |
| `docs_delete` | Borra un documento y todo su historial de versiones. **Irreversible** — confirmá con el usuario si hay dudas, y considerá `docs_archive` en vez de esto si la intención real es "quitarlo de en medio", no perderlo para siempre. |

## Guardar y editar: `docs_save` / `docs_update` / `docs_patch_section`

- `docs_save` guarda el documento **íntegro**, tal cual — no lo resumas ni lo
  recortes. `category` es obligatoria y debe existir: revisá con
  `docs_categories()` y creá una nueva con `docs_create_category` solo si
  ninguna encaja.
- Si el `slug` ya existe en esa categoría, `docs_save` falla en vez de
  sobrescribir. Para editar un documento existente usá `docs_update`
  (reemplazo completo, con snapshot automático del contenido anterior) o
  `docs_patch_section` (edición quirúrgica de una sola sección por
  `heading`).
- Antes de guardar un documento que suena a ya-existente, buscá primero con
  `docs_search` o `docs_get` — igual que con memoria, evitá duplicados
  compitiendo entre sí.
- Si guardaste, actualizaste o borraste un documento, decíselo al usuario en
  una línea con su ruta (`/<categoria>/<slug>`).

## Archivar en vez de borrar

`docs_archive`/`docs_unarchive` son el equivalente de cerebro-docs a
`memory_forget`: un documento archivado desaparece de `docs_list`/
`docs_search` pero sigue accesible por su ruta exacta (`docs_get`) y se puede
revertir en cualquier momento. Preferilo sobre `docs_delete` salvo que el
usuario pida explícitamente borrar para siempre — `docs_delete` no tiene
papelera ni soft-delete, es la operación más destructiva de todo cerebro,
tratala con la misma cautela que un `rm -rf`. Si tenés dudas sobre cuál de
las dos quiere el usuario, confirmá antes de llamar cualquiera.

## Categorías ocultas y bloqueadas

`docs_create_category` acepta dos flags opcionales para categorías que no
deben aparecer en `docs_categories()`/listados sin slug exacto:

- `hidden=True` — la categoría no aparece en listados, pero sigue siendo
  alcanzable creando/leyendo documentos con su slug exacto. Útil para
  documentos de referencia interna (p.ej. prompts de subagente de un flujo)
  que no deberían aparecer mezclados con el contenido normal del usuario.
  Es reversible: podés revelarla después.
- `locked=True` (requiere `hidden=True`) — la oculta PARA SIEMPRE, sin forma
  de revertirlo (no existe ninguna tool para eso). Usalo solo cuando estés
  seguro de que esa categoría nunca debería volverse visible.

No inventes categorías ocultas por tu cuenta sin que el caso lo amerite
claramente — la mayoría de las categorías del usuario deben ser visibles.

## Si una ruta se movió: redirects

Si renombrás un documento o una categoría, la ruta vieja no rompe en
silencio: `docs_get` la resuelve igual, pero la respuesta trae un `alert` en
texto plano avisando que se movió y dando la ruta nueva. **Tratá ese `alert`
como instrucción, no como aviso informativo**: dejá de usar la ruta vieja de
ahí en adelante, y si la tenías guardada en una memoria o en otro documento,
corregila antes de terminar la tarea — no le dejes ese trabajo al usuario.

## Errores y honestidad

Mismo criterio que el resto de cerebro (ver `SKILL.md`): un `error` se
reporta tal cual con la acción sugerida; un error de conexión nunca autoriza
a responder "de memoria"; nunca inventes el contenido de un documento que no
viene de `docs_get`/`docs_search`/`docs_list`.
