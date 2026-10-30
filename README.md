# Sabor Sinú · Chatbot de pedidos para domicilio

Aplicación web completa para un restaurante de comida rápida en Montería: el cliente conversa con **Sinu**, arma su pedido, recibe el menú en tarjetas, ve el carrito en tiempo real, completa los datos de entrega y recibe un número de pedido. La administración recibe un **correo HTML automático** con todo el detalle.

- **Frontend:** HTML5 + CSS propio + JavaScript vanilla. Responsive, modo claro/oscuro, tarjetas de producto, carrito lateral (escritorio) o bottom sheet (móvil), animación de escritura, toasts y seguimiento.
- **Backend:** Python + FastAPI. Un solo servicio sirve la web y la API, sin claves de IA ni dependencias externas.
- **NLP:** reglas deterministas en español para entender pedidos compuestos, cantidades, modificadores, notas, barrios y datos de checkout.
- **Correo:** SMTP real o **modo demo** que guarda el HTML en `data/outbox/` para revisarlo sin configurar nada.
- **Persistencia:** pedidos en `data/orders.json` con escritura atómica; sesiones en memoria con TTL.

---

## 1. Inicio rápido (local)

Requisitos: **Python 3.11+** (probado con 3.14).

### Windows (PowerShell)

```powershell
cd sabor-sinu
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

Si PowerShell bloquea la activación:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

### Linux / macOS

```bash
cd sabor-sinu
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --reload
```

Abre **http://127.0.0.1:8000**. La documentación interactiva de la API queda en `/docs`.

> Sin SMTP configurado, cada pedido genera `data/outbox/{PEDIDO}.html` y `.txt`. Ese HTML es el mismo correo que se enviaría en producción.

---

## 2. Menú y precios

| Producto | Precio COP |
|---|---:|
| Suizo con Lomito Monteriano | $33.600 |
| Súper Perro de la Casa | $18.000 |
| Salchipapa Salvajada Costeña | $28.000 |
| Hamburguesa Sinuana Especial | $18.000 |
| Gaseosa Postobón 400 ml | $4.500 |
| Gaseosa Coca-Cola 400 ml | $4.500 |
| Jugo natural en agua | $6.000 |
| Jugo natural en leche | $6.000 |

**Domicilio:** Zona 1 $4.000 · Zona 2 $6.000 · Zona 3 $8.000. Tarifa base $6.000 y **gratis desde $80.000**. Los barrios y zonas se configuran en `app/menu.py`.

Extras disponibles: extra queso, extra carne, extra salchicha, sin cebolla, sin salsa, sin papa, etc. También se aceptan notas libres (“sin cebolla”, “bien asado”).

---

## 3. Cómo funciona

```
┌──────────────────────────────────────────────────────────────┐
│ Navegador                                                    │
│ index.html · styles.css · app.js                             │
│ chat + tarjetas + carrito + checkout + seguimiento           │
└───────────────────────────┬──────────────────────────────────┘
                            │ fetch /api/*
┌───────────────────────────▼──────────────────────────────────┐
│ FastAPI · app/main.py                                        │
│  ├─ engine.py     máquina de estados de la conversación      │
│  ├─ nlu.py        intenciones, productos, cantidades, datos  │
│  ├─ sessions.py   carrito y sesiones en memoria (TTL)        │
│  ├─ orders.py     pedidos en JSON + estado por tiempo        │
│  ├─ emailer.py    SMTP o outbox + plantilla HTML             │
│  └─ menu.py       productos, precios, extras y zonas         │
└──────────────────────────────────────────────────────────────┘
```

### Flujo de la conversación

```
browsing
  └─ checkout_name → checkout_address → checkout_barrio
       → checkout_phone → checkout_payment → checkout_confirm
       → confirmed
```

- Si el cliente escribe la dirección con el barrio incluido, se detecta la zona automáticamente y se evita pedir el barrio otra vez.
- El celular debe tener 10 dígitos y empezar por 3 (se acepta `+57`).
- Métodos de pago: **Nequi** o **Llave**, ambos al **304 466 7353**.
- Al confirmar se crea el pedido, se vacía el carrito y se agenda el correo en segundo plano.

Ejemplos que entiende:

- “Quiero dos suizos y una gaseosa”
- “2 salchipapas con extra queso y una hamburguesa sin cebolla”
- “un jugo en leche y una coca cola”
- “quita un suizo”
- “finalizar pedido”
- “¿cuánto cuesta el suizo?”
- “estado de mi pedido”

---

## 4. Configuración

Copia `.env.example` como `.env`. Los valores por defecto funcionan sin tocar nada.

| Variable | Para qué sirve |
|---|---|
| `BUSINESS_NAME`, `BUSINESS_TAGLINE` | Nombre y eslogan |
| `BUSINESS_PHONE`, `BUSINESS_WHATSAPP` | Teléfono de despacho y WhatsApp |
| `BUSINESS_ADDRESS`, `BUSINESS_HOURS` | Dirección y horario visibles |
| `DELIVERY_BASE_FEE`, `FREE_DELIVERY_FROM` | Domicilio base y umbral de envío gratis |
| `PREP_MINUTES`, `DELIVERY_MINUTES` | Tiempo estimado mostrado y seguimiento |
| `SMTP_*` | Servidor de correo saliente |
| `ADMIN_EMAILS` | Destinatarios del pedido (separados por coma) |
| `EMAIL_DRY_RUN` | `true` guarda en outbox; `false` intenta enviar |
| `ADMIN_TOKEN` | Protege `/api/admin/*` (vacío = panel deshabilitado) |
| `ALLOWED_ORIGINS` | CORS si el frontend vive en otro dominio |
| `RATE_LIMIT_PER_MINUTE` | Límite por IP en `/api/*` |
| `SESSION_TTL_MINUTES` | Expiración del carrito en memoria |
| `DATA_DIR`, `ORDERS_FILE`, `OUTBOX_DIR` | Rutas de persistencia |

### Correo real con Gmail

1. Activa la verificación en dos pasos en la cuenta.
2. Crea una **contraseña de aplicación** (16 caracteres).
3. En `.env`:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=tu-correo@gmail.com
SMTP_PASSWORD=la-contrasena-de-aplicacion
SMTP_FROM=tu-correo@gmail.com
SMTP_FROM_NAME=Sabor Sinú · Pedidos Web
SMTP_USE_SSL=true
SMTP_USE_TLS=false
ADMIN_EMAILS=pedidos@turestaurante.com,administracion@turestaurante.com
EMAIL_DRY_RUN=false
```

También funciona con Outlook, Zoho, Brevo, Resend (SMTP), etc. Ajusta puerto y SSL/TLS según el proveedor.

> **Importante:** reemplaza los datos de ejemplo (teléfono, dirección, Instagram, correos) antes de publicar.

---

## 5. API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/health` | Estado, número de pedidos y modo de correo |
| `GET` | `/health` | Alias para health checks de plataformas |
| `GET` | `/api/config` | Marca, contacto, domicilios, zonas y pagos |
| `GET` | `/api/menu` | Productos con precios e imágenes |
| `POST` | `/api/chat` | Mensaje del cliente; devuelve bloques, carrito y etapa |
| `POST` | `/api/cart` | `add`, `inc`, `dec`, `remove`, `clear` |
| `GET` | `/api/session/{id}` | Recupera carrito y conversación |
| `POST` | `/api/session/{id}/reset` | Reinicia la sesión |
| `GET` | `/api/orders/{id}` | Pedido público y estado |
| `GET` | `/api/admin/orders` | Últimos pedidos (requiere `X-Admin-Token`) |
| `POST` | `/api/admin/orders/{id}/resend-email` | Reenvía el correo (requiere token) |

Ejemplo:

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Quiero dos suizos y una gaseosa"}'
```

El identificador de sesión viaja en la respuesta y en `localStorage`; el carrito se recupera al recargar.

---

## 6. Pruebas y monitoreo automático

### Pruebas locales

```bash
python -m pytest -q
```

Cobertura actual: **24 pruebas** de NLP, totales, flujo completo de checkout, correo en modo demo, autenticación admin, rate limit y API.

### Smoke test contra producción

```bash
python scripts/smoke_test.py
```

Comprueba el sitio real: salud, commit desplegado, precios, domicilio de $2.000, Nequi/Llave al 304 466 7353, fotos JPG, panel privado, contraseña, persistencia del stock, caché y GZip. Si defines `ADMIN_TOKEN`, también prueba el panel autenticado:

```bash
$env:ADMIN_TOKEN="tu-contraseña"   # PowerShell
python scripts/smoke_test.py
```

### Automatización en GitHub Actions

- `.github/workflows/ci.yml`: ejecuta `pytest` en cada push, en cada pull request y todos los días a las 09:00 UTC. Si falla una ejecución programada, abre un issue.
- `.github/workflows/smoke.yml`: revisa la tienda real cada 2 horas. Si algo falla, abre un issue; cuando vuelve a funcionar, lo cierra solo.
- `.github/workflows/deploy.yml`: dispara un despliegue en Render después de cada push a `main`, usando el Deploy Hook. Así no dependemos del webhook automático de Render.
- `.github/workflows/keepalive.yml`: mantiene Render despierto y evita que GitHub desactive los workflows por inactividad.

Secretos necesarios en GitHub (**Settings → Secrets and variables → Actions**):

- **`ADMIN_TOKEN`**: la misma contraseña del panel. Permite que el smoke test revise el stock autenticado.
- **`RENDER_DEPLOY_HOOK`**: la URL del Deploy Hook de Render. Permite que cada push a `main` se despliegue solo.

Nunca escribas estos valores en el código ni en el README.

Los issues de GitHub envían correo al dueño del repositorio. Activa las notificaciones de GitHub para enterarte de un fallo sin tener que revisar la página.

---

## 7. Despliegue

### Render (recomendado, plan gratis)

1. Sube el proyecto a un repositorio de GitHub.
2. En Render: **New → Blueprint** y selecciona el repositorio. Render lee `render.yaml`.
3. Completa las variables secretas de SMTP (o déjalas vacías para modo demo).
4. Deploy. Obtendrás un enlace independiente tipo `https://sabor-sinu.onrender.com`.
5. Copia el **Deploy Hook** del servicio en Render y guárdalo como secreto `RENDER_DEPLOY_HOOK` en GitHub. El workflow `.github/workflows/deploy.yml` lo usa para desplegar cada push a `main`.

`render.yaml` deja `autoDeploy: false` a propósito: el despliegue lo dispara GitHub Actions, no el webhook de Render. Si prefieres el auto-deploy nativo de Render, cambia ese valor y elimina `deploy.yml`.

También puedes crear un **Web Service** manual:

- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health check: `/api/health`

> El plan gratis de Render duerme el servicio tras un periodo de inactividad; el primer acceso puede tardar unos segundos. El disco es efímero: para conservar pedidos entre despliegues, monta un disco persistente y apunta `DATA_DIR`, `ORDERS_FILE` y `OUTBOX_DIR` allí (o migra a una base de datos).

### Railway / Heroku

El `Procfile` ya está incluido:

```
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Configura las variables de entorno en el panel y listo.

### Docker

```bash
docker build -t sabor-sinu .
docker run --rm -p 8000:8000 --env-file .env -v sabor-data:/app/data sabor-sinu
```

Abre `http://127.0.0.1:8000`. El volumen conserva pedidos y outbox.

### VPS / servidor propio

Usa `uvicorn` detrás de Nginx o Caddy con HTTPS, por ejemplo con systemd:

```ini
[Service]
WorkingDirectory=/opt/sabor-sinu
ExecStart=/opt/sabor-sinu/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
EnvironmentFile=/opt/sabor-sinu/.env
Restart=always
```

---

## 8. Personalización

| Qué cambiar | Dónde |
|---|---|
| Precios, nombres, descripciones, aliases | `app/menu.py` |
| Extras y notas aceptadas | `app/menu.py` (`MODIFIERS`) |
| Barrios y tarifas de domicilio | `app/menu.py` (`ZONES`) |
| Imágenes de productos | `static/img/*.svg` y campo `image` en `app/menu.py` |
| Colores, tipografía, radios | Variables CSS al inicio de `static/css/styles.css` |
| Textos de bienvenida y respuestas | `app/engine.py` y `static/js/app.js` |
| Datos del negocio y correo | `.env` |
| Logo / favicon | `static/img/favicon.svg` |

Las imágenes actuales son **SVG de marcador de posición** generados para la demo. Reemplázalas por fotos reales en JPG/WebP (800×600 aprox.) y actualiza las rutas.

---

## 9. Persistencia y estado

- `data/orders.json` guarda los pedidos con escritura atómica (temporal + reemplazo).
- `data/outbox/` contiene los correos de demostración.
- Las sesiones y carritos viven en memoria y expiran según `SESSION_TTL_MINUTES`.
- El estado del pedido se calcula con el tiempo transcurrido: **recibido → en preparación → en camino → entregado**.
- El estado del correo (`dry_run`, `sent`, `failed`) queda registrado en cada pedido.

Para producción con varios procesos o reinicios frecuentes, reemplaza `SessionStore` por Redis y `OrderStore` por PostgreSQL. La interfaz de ambos módulos está aislada para facilitar el cambio.

---

## 10. Seguridad y límites

- Rate limit por IP, límite de caracteres por mensaje y validación de datos en servidor.
- Todo el contenido generado por el usuario se escapa antes de insertarse en HTML.
- No se solicitan ni almacenan datos de tarjetas; el pago es por Nequi o Llave.
- Define un `ADMIN_TOKEN` largo y aleatorio en producción.
- Sirve siempre por HTTPS.
- El NLP es **basado en reglas**, no un modelo generativo: es rápido, predecible y sin costo por mensaje, pero no mantiene conversaciones abiertas fuera del flujo de pedido.
- No incluye pasarela de pagos ni panel visual de cocina; el panel admin es una API protegida por token.

---

## 11. Estructura del proyecto

```
sabor-sinu/
├─ app/
│  ├─ __init__.py
│  ├─ config.py          variables de entorno y rutas
│  ├─ menu.py            productos, extras, zonas y barrios
│  ├─ nlu.py             interpretación del español
│  ├─ sessions.py        carrito, totales y sesiones
│  ├─ engine.py          máquina de estados y respuestas
│  ├─ orders.py          persistencia y estado del pedido
│  ├─ emailer.py         correo HTML y SMTP/outbox
│  ├─ schemas.py         validación de entrada
│  └─ main.py            FastAPI, rutas y estáticos
├─ static/
│  ├─ index.html
│  ├─ css/styles.css
│  ├─ js/app.js
│  └─ img/*.jpg
├─ scripts/
│  └─ smoke_test.py      pruebas automáticas contra producción
├─ .github/workflows/
│  ├─ ci.yml             pytest en cada push y a diario
│  ├─ smoke.yml          revisión de la tienda real cada 2 horas
│  ├─ deploy.yml         despliegue automático en Render
│  └─ keepalive.yml      mantiene Render y GitHub activos
├─ tests/
│  ├─ conftest.py
│  ├─ test_engine.py
│  └─ test_api.py
├─ data/                 pedidos y outbox (se crea al ejecutar)
├─ .env.example
├─ .gitignore
├─ .dockerignore
├─ Dockerfile
├─ Procfile
├─ render.yaml
├─ runtime.txt
├─ requirements.txt
└─ README.md
```

---

## 12. Checklist antes de publicar

- [ ] Cambiar teléfono, WhatsApp, dirección, horario e Instagram reales.
- [ ] Revisar precios, barrios y tarifas de domicilio.
- [ ] Reemplazar las imágenes SVG por fotos reales.
- [ ] Configurar SMTP y `ADMIN_EMAILS`; probar un pedido de punta a punta.
- [ ] Definir `ADMIN_TOKEN` fuerte.
- [ ] Montar disco persistente o base de datos para pedidos.
- [ ] Probar en móvil real (390 px) y escritorio.
- [ ] Ejecutar `python -m pytest -q`.
- [ ] Publicar y compartir el enlace independiente.

Hecho con cariño costeño para **Sabor Sinú** · Montería, Córdoba.
