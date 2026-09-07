<div dir="rtl">

# خدمة ربط الماسح الضوئي (Scanner Bridge Service) بتطبيقات الويب عن طريق API

<p align="right">
  <a href="#scanner-bridge-service">English</a> | العربية
</p>

خدمة محلية تعمل على **ويندوز** فقط، تتواصل مع الماسح الضوئي عبر واجهة **WIA**
(Windows Image Acquisition — مدمجة في ويندوز، لا تحتاج أي SDK إضافي للماسح)،
وتوفّرها عبر HTTP على **المنفذ 5000**. تكتشف تلقائيًا هل تمسح من السطح
المسطّح (Flatbed) أو من وحدة التغذية التلقائية للأوراق (ADF)، وتُرجع الصفحة
الممسوحة كـ **صورة PNG بترميز base64**، وتعرض **أيقونة في شريط النظام**
تكون خضراء عند اتصال الماسح، وحمراء عند عدم اتصاله.

> **لويندوز فقط.** واجهة WIA خاصة بويندوز، ولن تعمل هذه الأداة على
> macOS أو Linux.

## ١. التثبيت والتشغيل من الكود المصدري

```bat
run.bat
```

هذا الملف ينشئ بيئة افتراضية (venv)، يثبّت المتطلبات، ويشغّل الخدمة.
ستظهر أيقونة في شريط النظام (حمراء حتى يتم العثور على ماسح ضوئي).

أو يدويًا:

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python scanner_service.py
```

## ٢. واجهة برمجة التطبيقات (API)

### `GET /status`
تُرجع حالة الاتصال الحالية.

```json
{
  "connected": true,
  "device_name": "HP OfficeJet Pro 8025",
  "supports_flatbed": true,
  "supports_adf": true,
  "last_error": null,
  "last_checked": 1735689600.123
}
```

### `POST /scan`
تقوم بمسح صفحة وإرجاعها بترميز base64. **لا حاجة لإرسال أي بيانات (body)** —
فقط أرسل طلب POST فارغًا وستتم عملية المسح تلقائيًا بالإعدادات الافتراضية
(`source: auto`، `dpi: 200`):

```bash
curl -X POST http://127.0.0.1:5000/scan
```

إذا أردت تغيير الإعدادات الافتراضية، يمكنك اختياريًا إرسال JSON أو معطيات
في الرابط (query params):

```json
{ "dpi": 300, "source": "adf" }
```

- `dpi` — الدقة، الافتراضي `200`.
- `source` — `"auto"` (افتراضي)، أو `"flatbed"`، أو `"adf"`.
  - وضع `auto` يتحقق أولًا من وحدة التغذية (ADF): إن كانت هناك ورقة محمّلة
    فيها، يمسح منها؛ وإلا يعود للمسح من السطح المسطّح.

الاستجابة:

```json
{
  "success": true,
  "source_used": "adf",
  "dpi": 300,
  "format": "png",
  "image_base64": "iVBORw0KGgoAAAANSUhEUgAA..."
}
```

عند حدوث خطأ (انحشار ورق، عدم وجود ورق، الجهاز غير متصل، مشغول، إلخ):

```json
{ "success": false, "error": "No paper detected in the ADF feeder." }
```

يمكنك أيضًا تجربتها مباشرة من المتصفح عبر GET:
`http://127.0.0.1:5000/scan?dpi=300&source=auto`

### `GET /health`
فحص بسيط للتأكد من أن الخدمة تعمل: `{"ok": true}`.

## ٣. معالجة الأخطاء

يتم ترجمة أعطال الماسح الضوئي الشائعة إلى رسائل واضحة (انحشار ورق، عدم وجود
ورق في وحدة التغذية، غطاء مفتوح، الجهاز مشغول/غير متصل/في طور التسخين،
إعدادات غير مدعومة، خطأ عام في الاتصال بالجهاز). أي خطأ غير متوقع يتم
التقاطه وإرجاعه كاستجابة `500`/`502` مع نص الخطأ، بدلًا من توقف الخدمة عن
العمل. **لا يوجد ملف سجلّ (log file) عن قصد** — لأن أي سجلّ يعمل بلا توقف
سينمو باستمرار وقد يُبطئ الجهاز مع الوقت. الحالة الحالية (بما في ذلك آخر
خطأ إن وجد) متوفرة دائمًا في الذاكرة عبر `GET /status`، وتُطبع الرسائل في
الطرفية (console) إن كانت متاحة، ولا تظهر عند التشغيل كملف `.exe` بدون نافذة.

## ٤. أيقونة شريط النظام

- تظهر أيقونة `green.ico` عندما يكون الماسح الضوئي متصلًا ويمكن الوصول إليه
  عبر WIA.
- تظهر أيقونة `red.ico` عند عدم العثور على ماسح ضوئي أو حدوث خطأ في الاتصال
  (مرّر الفأرة فوق الأيقونة، أو راجع `/status`، لمعرفة السبب).
- انقر بزر الفأرة الأيمن على الأيقونة للوصول إلى **"Open status page"**
  و **"Quit"**.
- يجب أن يكون ملفا `.ico` بجانب `scanner_service.py` (أو مضمَّنين داخل ملف
  الـ exe — راجع خطوة البناء أدناه). إذا كان أحد الملفين مفقودًا، تعرض
  الأداة نقطة ملونة بسيطة بدلًا من التوقف عن العمل.

يتم تحديث الحالة تلقائيًا كل ٤ ثوانٍ تقريبًا.

## ٥. تحويلها إلى ملف exe مستقل

نفّذ هذا **على ويندوز**، مع تفعيل البيئة الافتراضية (venv) من الخطوة ١:

```bat
build.bat
```

يستخدم هذا PyInstaller لإنتاج `dist\ScannerBridge.exe` — ملف واحد، بدون
نافذة طرفية (أيقونة شريط النظام فقط)، مع تضمين `green.ico`/`red.ico` داخل
الملف نفسه (عبر `--add-data`) بحيث تعمل الأيقونة حتى لو كان الملف التنفيذي
واحدًا فقط. عند النقر المزدوج عليه، يبدأ تشغيل خادم HTTP على المنفذ 5000
وتظهر أيقونة شريط النظام؛ لا حاجة لتثبيت Python على الجهاز المستهدف.


## ٦. التشغيل التلقائي عند بدء تشغيل ويندوز

تقوم الخدمة بتسجيل نفسها في سجلّ ريجستري المستخدم الحالي (Registry Run key)
(`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`، باسم
القيمة `LocalScannerAPI`) في كل مرة تبدأ فيها، مشيرة إلى مسار ملف `.exe`
الجاري تشغيله (أو مسار `.py` إن كانت تعمل من الكود المصدري). هذا يعني أنها
ستُشغَّل تلقائيًا في المرة القادمة التي تسجّل فيها الدخول إلى ويندوز — دون
الحاجة لاختصار منفصل في مجلد بدء التشغيل. يفشل التسجيل بصمت إذا تعذّرت
الكتابة في الريجستري (مثلًا بسبب صلاحيات مقيّدة)، بحيث لا يمنع ذلك الأداة
من العمل أبدًا.

لإزالتها من قائمة بدء التشغيل، احذف القيمة `LocalScannerAPI` من مفتاح
الريجستري يدويًا (`regedit`)، أو نفّذ:

```bat
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v LocalScannerAPI /f
```

## ٧. ملاحظات وحدود الأداة

- يتم استهداف ماسح ضوئي واحد فقط في كل مرة (أول ماسح تعرضه WIA). إذا كان
  لديك أكثر من ماسح ضوئي متصل، عدّل دالة `find_first_scanner()` للتصفية
  حسب الاسم أو المعرّف.
- اكتشاف وجود ورق في وحدة التغذية (ADF) يعتمد على أن يبلّغ التعريف (driver)
  بشكل صحيح عن `Document Handling Status`؛ بعض التعريفات البسيطة جدًا لا
  تدعم هذا وستُعامَل دائمًا كأنها تدعم السطح المسطّح فقط.
- إذا ظهرت رسالة من جدار حماية ويندوز عند أول تشغيل، اسمح بالوصول حتى تتمكن
  أجهزة أخرى على شبكتك من الوصول إلى المنفذ 5000 (أو امنعه إذا كنت تريد
  الوصول من نفس الجهاز فقط — غيّر `HOST` في `scanner_service.py` إلى
  `"127.0.0.1"`).

</div>

---

<a name="scanner-bridge-service"></a>
# Scanner Bridge Service

A local Windows service that talks to your scanner via **WIA** (Windows Image
Acquisition — built into Windows, no extra scanner SDK needed) and exposes it
over HTTP on **port 5000**. It auto-detects whether to scan from the flatbed
or the ADF (automatic document feeder), returns the scanned page as a
**base64 PNG**, and shows a **tray icon** that's green when a scanner is
connected and red when it isn't.

> **Windows only.** WIA is a Windows API. This will not run on macOS/Linux.

## 1. Install & run from source

```bat
run.bat
```

This creates a virtual environment, installs dependencies, and starts the
service. You'll see a tray icon appear (red until a scanner is found).

Or manually:

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python scanner_service.py
```

## 2. API

### `GET /status`
Returns current connection state.

```json
{
  "connected": true,
  "device_name": "HP OfficeJet Pro 8025",
  "supports_flatbed": true,
  "supports_adf": true,
  "last_error": null,
  "last_checked": 1735689600.123
}
```

### `POST /scan`
Scans a page and returns it as base64. **No body is required** — just POST
with nothing attached and it scans using the defaults (`source: auto`,
`dpi: 200`):

```bash
curl -X POST http://127.0.0.1:5000/scan
```

If you want to override the defaults, you can optionally send a JSON body or
query params:

```json
{ "dpi": 300, "source": "adf" }
```

- `dpi` — resolution, default `200`.
- `source` — `"auto"` (default), `"flatbed"`, or `"adf"`.
  - `auto` checks the ADF first: if it has paper loaded, it scans from the
    ADF; otherwise it falls back to the flatbed.

Response:

```json
{
  "success": true,
  "source_used": "adf",
  "dpi": 300,
  "format": "png",
  "image_base64": "iVBORw0KGgoAAAANSUhEUgAA..."
}
```

On failure (jam, no paper, offline, busy, etc.):

```json
{ "success": false, "error": "No paper detected in the ADF feeder." }
```

You can also call it via a browser/GET for quick testing:
`http://127.0.0.1:5000/scan?dpi=300&source=auto`

### `GET /health`
Simple liveness check: `{"ok": true}`.

## 3. Error handling

Common scanner failure modes are translated into plain-English messages
(paper jam, no paper in ADF, cover open, scanner busy/offline/warming up,
unsupported settings, general I/O error). Any unexpected exception is caught
and returned as a `500`/`502` with the error text rather than crashing the
service. There is intentionally **no log file** — a log that runs forever
would keep growing and could eventually slow the machine down. The current
status (including the last error, if any) is always available in memory via
`GET /status`, and messages print to the console if one is attached (not
when running as the windowed `.exe`).

## 4. Tray icon

- `green.ico` is shown while a scanner is connected and reachable via WIA.
- `red.ico` is shown when no scanner is found or a connection error occurred
  (hover over the icon, or check `/status`, to see the reason).
- Right-click the icon for **"Open status page"** and **"Quit"**.
- Both `.ico` files must sit next to `scanner_service.py` (or be bundled into
  the exe — see the build step below). If either file is missing, the app
  falls back to a plain colored dot instead of crashing.

Status is refreshed automatically every ~4 seconds.

## 5. Packaging into a standalone .exe

Run this **on Windows**, with the venv from step 1 active:

```bat
build.bat
```

This uses PyInstaller to produce `dist\ScannerBridge.exe` — a single file,
no console window (tray icon only), with `green.ico`/`red.ico` bundled inside
it (via `--add-data`) so the tray icon still works even though the exe is
just one file. Double-clicking it starts the HTTP server on port 5000 and
shows the tray icon; no Python installation is required on the target
machine.



## 6. Auto-start on Windows boot

The service registers itself in the current user's Registry Run key
(`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`, value
name `LocalScannerAPI`) every time it starts, pointing at the running
`.exe` (or the `.py` path if run from source). This means it will
automatically launch again the next time you log into Windows — no separate
shortcut in the Startup folder needed. Registration fails silently if the
Registry key can't be written (e.g. restricted permissions), so it never
blocks the app from running.

To remove it from startup, delete the `LocalScannerAPI` value from that
Registry key manually (`regedit`), or run:

```bat
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v LocalScannerAPI /f
```

## 7. Notes / limitations

- Only one scanner is targeted at a time (the first one WIA reports). If you
  have multiple scanners connected, extend `find_first_scanner()` to filter
  by name/ID.
- ADF paper detection relies on the driver correctly reporting
  `Document Handling Status`; a few very basic drivers don't support this and
  will always be treated as flatbed-only.
- If Windows Firewall prompts on first run, allow access so other machines on
  your network can reach port 5000 (or block it if you only want localhost
  access — change `HOST` in `scanner_service.py` to `"127.0.0.1"`).
