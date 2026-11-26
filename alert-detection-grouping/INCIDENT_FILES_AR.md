# ملفات الحادثة وأداة قراءة الأدلة

تحديث 7 نوفمبر 2026. افتح `reports/index.md`، ثم اختر الحادثة.

## أنا أقرأ إيه والـagent يستقبل إيه؟

- أنت افتح `summary.md`: ملخص قصير، التنبيهات التابعة للحادثة، أسباب التجميع، ونواقص الأدلة.
- الـagent يبدأ بملف `incident.json`: بيانات منظمة صغيرة، وفيها `snapshot_id` و`evidence_refs`.
- `evidence/logs.jsonl` فيه سجل JSON مستقل في كل سطر.
- `evidence/metrics.json` فيه قيم التنبيهات وقت البداية وآخر قراءة، والاستعلامات والوحدات والفترات. مش تاريخ كل القياسات.
- `evidence/traces/` فيه ملف JSON لكل نسخة trace محفوظة، و`index.json` يحدد العينات التابعة لآخر snapshot.

تخيّل الـagent قرأ الملخص وعايز يفحص استدعاء Orders لـPayments. يطلب ref اسمه `logs` فيأخذ صفحة صغيرة. بعد كده يطلب `traces` فيأخذ قائمة الرحلات ومعرّفاتها. يختار رحلة، ويطلب ref الخاص بها لقراءة خطواتها. مش محتاج نرسل كل الأدلة في أول رسالة.

## ليه فيه snapshots و.objects؟

الحادثة بتتحدث. لو الـagent بدأ على نسخة معينة، لازم الدليل ما يتغيرش تحت إيده. لذلك `snapshots/` يحفظ مدخل كل نسخة وخريطة مراجعها، و`evidence/.objects/` يحتفظ بنسخ الأدلة الثابتة. المحتوى المطابق يُعاد استخدامه بدل إعادة كتابة نسخ متطابقة.

ملفات الأدلة بالأسماء البسيطة تعرض أحدث حالة للقراءة اليدوية؛ أدوات الـagent تعتمد خريطة النسخة المحفوظة. ما تمسحش `.objects` أو snapshots عشوائيًا. الحذف التلقائي للنسخ القديمة مش مطبّق. وملفات trace القديمة قد تظل موجودة لحماية المراجع؛ قائمة آخر نسخة في `traces/index.json`.

البيانات محفوظة محليًا، لا يوجد استعلام live داخل أداة القراءة. لو ref مفقود أو البصمة مختلفة، الأداة تبلغ بخطأ بدل ما ترجع بيانات من وقت مختلف. حالة المصادر الموجودة في الملف تصف وقت الالتقاط، مش صحة النظام الآن.

## ربط الأداة بالـagent القادم

الكود التالي مثال Python لفريق المرحلة التالية. حدد REPORTS من إعدادات التطبيق؛ الـmodel يختار الحادثة والنسخة والمرجع، وليس مسارًا حرًا على الجهاز.

```python
from pathlib import Path
from observa_alerts.evidence import read_evidence

REPORTS = Path("reports").resolve()

def get_incident_evidence(incident_id, snapshot_id, ref_id, cursor=0, limit=20):
    return read_evidence(
        REPORTS, incident_id, snapshot_id, ref_id,
        cursor=cursor, limit=limit, max_bytes=16384,
    )
```

اربط هذه الدالة كنقطة استدعاء في إطار الـagent اللي ستستخدمه. لم تتم إضافة agent تحليل أو MCP server ضمن هذه الميزة.

قيم refs الرئيسية: `logs`، `metrics`، `traces`، `relationships`، `timeline`، `details`.
`relationships` يحمل أدلة الربط التفصيلية، و`details` يحتفظ بالتفاصيل الأصلية للتنبيهات ودليل البداية وتاريخها. سجل التنبيه هناك فيه `alert_id` عشان تختار المطلوب.

الرد فيه `items` و`next_cursor` و`has_more` وعدد السجلات وأصل البيانات والفترة. كرر الطلب بنفس snapshot والمرجع مع next_cursor لاستكمال الصفحة التالية.
الأداة لا تقفز فوق سجل كبير: ترجع `record_too_large`؛ زوّد ميزانية الرد إن أمكن ضمن السقف 65536 byte، أو تعامل معه كدليل لم يُقرأ. الحد بالـbytes وليس تقدير tokens.

## تجربة صغيرة من Terminal

من فولدر الميزة، المثال ده يختار أول حادثة تلقائيًا ويقرأ أول خمسة logs:

```powershell
$incident = Get-ChildItem reports/INC-*/incident.json | Select-Object -First 1
$i = Get-Content $incident.FullName -Raw | ConvertFrom-Json
python -m observa_alerts.evidence --incident $i.id --snapshot $i.snapshot_id --ref logs --limit 5
```

لو مفيش Python على الجهاز، نفس أداة القراءة موجودة في صورة الميزة بعد بنائها:

```powershell
docker compose exec -T alerts python -m observa_alerts.evidence --incident $i.id --snapshot $i.snapshot_id --ref logs --limit 5
```

لتصفح الرحلات، استبدل `logs` بـ`traces`، ثم استخدم ref_id من النتيجة لطلب خطوات الرحلة.

## الترقية والحفظ

الإعدادات وSQLite وقواعد detection/grouping كما هي. تغيير تسليم الملفات هو v2؛ المستهلك القديم اللي كان يقرأ evidence داخل incident مباشرة لازم يتحدث ليقرأ المراجع.

الحوادث القديمة تتحوّل بأداة `python -m observa_alerts.repack --reports reports` والعامل متوقف. النسخ الأصلية تنتقل إلى `_legacy/` بعد نجاح التحقق، ولا تُحذف. لو العملية انقطعت، النسخ التي لم تُنقل تظل مكانها ويمكن إعادة تشغيل التحويل.

بعد تعديل الكود، الحاوية القديمة لا تتحدث وحدها. مع Docker شغال، من فولدر الميزة:

```powershell
docker compose up --build -d
```

كل مجلد reports، بما فيه النسخ والأرشيف، مستبعد من Git. للاحتفاظ بنسخة تسليم خارج الجهاز انسخ فولدر الحادثة كاملًا، لأن نقل incident.json وحده لا ينقل الأدلة المشار إليها.
