# I did it — بوت تيليجرام لمتابعة الإنجاز اليومي

بوت شخصي يذكّرك بمهامك اليومية المسجّلة في Notion، **ويسأل كل مهمة بالطريقة المناسبة لها** (نعم/لا، رقم، تقييم 1-5، أو ملاحظة نصية)، مع تقرير تقدم وإحصائيات.

- ⏰ قائمة مهام تلقائية الساعة **4 فجراً** وتذكيرات الساعة **4 عصراً** و **11 ليلاً** (Asia/Riyadh)
- 📋 يقرأ مهام اليوم حسب خاصية `Days` + خاصية `Type` لتحديد نوع السؤال
- 📊 أمر `/report` يعرض إحصائية التقدم لآخر 30 يوم أو عدد أيام تحدده
- 🧩 **قابل للتوسعة**: تقدر تضيف أنواع أسئلة جديدة بـ ~30 سطر Python
- ✅ ❌ ⏭️ أزرار للحالة + إدخال قيم مخصصة حسب النوع
- 🐳 Docker للنشر على VPS مع إعادة تشغيل تلقائية

---

## أنواع الأسئلة المتاحة

| القيمة في خاصية Type | شكل السؤال | ما يُكتب في Notion |
|----------------------|-----------|-------------------|
| (فارغ) أو `Boolean` | 3 أزرار: ✅ تم / ❌ لم يتم / ⏭️ بكرة | `Status` |
| `Number` | زر «أدخل الرقم» → ترد بالرقم | `Status=Done` + `Value` (Number) |
| `Rating` | 5 أزرار 1️⃣2️⃣3️⃣4️⃣5️⃣ + ❌ ⏭️ | `Status=Done` + `Value` (Number) |
| `Text` | زر «أدخل الملاحظة» → ترد بالنص | `Status=Done` + `Note` (Rich Text) |

في كل الحالات، أزرار ❌ و ⏭️ متاحة لتسجيل المهمة كـ Missed أو ترحيلها لبكرة.

> **إضافة نوع جديد لاحقاً**: راجع [`bot/answer_types.py`](bot/answer_types.py) — أضف Subclass + سطر `REGISTRY.register(...)` + قيمة جديدة في خاصية `Type` بـ Notion.

---

## بنية قاعدة Notion المطلوبة

يمكنك استخدام قاعدة واحدة كما في الإعداد الحالي:

- الصفوف التي `Date` فيها فارغ = مهام دائمة.
- الصفوف التي `Date` فيها تاريخ = سجل يومي وإجابات.
- ضع نفس الـ Database ID في `NOTION_DATABASE_ID` و `NOTION_LOG_DATABASE_ID`.

### خصائص المهام الدائمة

| الخاصية | النوع | استخدامها |
|---------|------|----------|
| `Name` | Title | عنوان المهمة |
| `Type` | Select | بقيم: `Boolean`, `Number`, `Rating`, `Text` |
| `Days` | Multi-select | أيام المهمة: `السبت`, `الأحد`, ... ويعني الفراغ = كل يوم |

### خصائص سجل اليوم

| الخاصية | النوع | استخدامها |
|---------|------|----------|
| `Name` | Title | عنوان المهمة كما ظهر ذلك اليوم |
| `Date` | Date | تاريخ السجل |
| `Status` | Status أو Select | بقيم: `Done`, `Missed`, `Postponed` |
| `Value` | Number | للأرقام والتقييمات |
| `Note` | Rich Text | للملاحظات النصية |

> أسماء الخواص وقيم الـ Select **كلها قابلة للتعديل** عبر `.env` لو قاعدتك بأسماء مختلفة. خواص `Value` و `Note` اختيارية — لو ما عندك مهام رقمية/نصية لا تحتاجها.

---

## الإعداد لمرة واحدة

### 1) إنشاء بوت تيليجرام

افتح [@BotFather](https://t.me/BotFather) → `/newbot` → اختر اسم → احفظ التوكن.

### 2) إعداد Notion

1. https://www.notion.so/profile/integrations → **New integration** → نوع **Internal** → احفظ `NOTION_TOKEN`.
2. افتح قاعدة المهام → ⋯ → **Connections** → اختر التكامل.
3. اربط التكامل بقاعدة `I_did_it`.
4. انسخ Database ID وضعه في `.env` في `NOTION_DATABASE_ID` و `NOTION_LOG_DATABASE_ID`.
5. تأكد أن الخواص أعلاه موجودة في القاعدة.

### 3) عبّئ `.env`

```bash
cp .env.example .env
nano .env  # ضع TELEGRAM_BOT_TOKEN, NOTION_TOKEN, NOTION_DATABASE_ID, NOTION_LOG_DATABASE_ID
```

اترك `TELEGRAM_CHAT_ID` فارغ في أول مرة.

### 4) اختبار محلي

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m bot.main
```

في تيليجرام: ابحث عن بوتك → `/start` → سيرد بـ Chat ID. ضعه في `.env`:

```
TELEGRAM_CHAT_ID=123456789
```

أعد تشغيل البوت. ثم جرّب:
- `/health` — يتأكد من Notion ويعرض حالة الخواص (Type/Value/Note ✓ أو ✗)
- `/tasks` — يعرض مهام اليوم الآن
- `/report` — يعرض تقرير آخر 30 يوم

---

## النشر على الـ VPS (Ubuntu + Docker)

```bash
git clone <repo-url> /opt/i_did_it
cd /opt/i_did_it
cp .env.example .env
nano .env

docker compose up -d --build
docker compose logs -f bot
```

تحديث لاحق:

```bash
cd /opt/i_did_it && git pull && docker compose up -d --build
```

تحقق من إعادة التشغيل التلقائية:

```bash
docker compose ps                 # Status: Up
sudo reboot                       # تأكد أن البوت يعود تلقائياً بعد reboot
```

---

## الأوامر داخل البوت

| الأمر | الوظيفة |
|------|---------|
| `/start` | ترحيب + عرض Chat ID في أول استخدام |
| `/tasks` | عرض مهام اليوم المفتوحة الآن |
| `/report` | تقرير التقدم لآخر 30 يوم. مثال: `/report 7` |
| `/add` | إضافة مهمة مع نوع الإجابة وأيام الأسبوع |
| `/list` | عرض المهام الدائمة مع الأيام |
| `/edit` | تعديل الاسم أو النوع أو الأيام |
| `/delete` | حذف مهمة دائمة |
| `/health` | تأكيد الاتصال بـ Notion + حالة الخواص + موعد التذكير القادم |

---

## استكشاف الأخطاء

- **البوت لا يرد**: تأكد من `TELEGRAM_BOT_TOKEN`، وراجع `docker compose logs bot`.
- **`Notion FAIL`**: تأكد من ربط الـ Integration بقاعدة البيانات (Connections).
- **`Property X is missing`**: راجع أسماء الخواص في `.env` لتطابق Notion بالضبط.
- **التذكير ما يجي**: تأكد من `TIMEZONE=Asia/Riyadh` و `TELEGRAM_CHAT_ID` معبّأ، وراجع `MORNING_HOUR` و `REMINDER_HOURS`.
- **سؤال نوع غير معروف**: قيمة `Type` في الصف لا تطابق المسجّلة. البوت يرجع للنوع الافتراضي (Boolean) ويسجّل تحذير في الـ logs.

---

## بنية المشروع

```
I_did_it/
├── bot/
│   ├── main.py            # نقطة الدخول + lifecycle
│   ├── config.py          # متغيرات البيئة
│   ├── notion_client.py   # طبقة Notion
│   ├── answer_types.py    # registry قابل للتوسعة لأنواع الأسئلة
│   ├── handlers.py        # أوامر + أزرار + ردود نصية
│   ├── days.py            # أسماء أيام الأسبوع وتحليل إدخال المستخدم
│   └── scheduler.py       # 04:00 + 16:00 + 23:00
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```
