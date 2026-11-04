# Airitom Free Zone — nazorat markazi

## GitHub va Render orqali ishga tushirish

1. Yetkazilgan ZIP ichidagi fayllarni ochib, GitHub repository **ildiziga** yuklang. Shu darajada `app.py`, `freezone.py`, `schema.sql`, `requirements.txt` va `render.yaml` ko‘rinishi kerak. `templates/`, `static/` va test uchun `.github/` papkalarini ichidagi fayllari bilan saqlang.
2. Mavjud tarozi tarixini davom ettirish uchun shu bazaning PostgreSQL zaxirasini oling va uning `DATABASE_URL` manzilini ishlating. Yangi o‘rnatishda bo‘sh PostgreSQL baza ishlatish mumkin.
3. Render → New → Blueprint orqali repository’ni ulang. `DATABASE_URL`ga Supabase pooler manzilini kiriting. `INITIAL_TECHADMIN_PASSWORD` ixtiyoriy: kamida 8 belgili qiymat bersangiz birinchi parol shu bo‘ladi; bo‘sh qolsa dastur xavfsiz tasodifiy vaqtinchalik parol yaratib, Render logida `AIRITOM FIRST LOGIN` qatorida bir marta ko‘rsatadi. Blueprint yangi servis uchun `SECRET_KEY` yaratadi. Mavjud servisda amaldagi kalitni saqlang. `/health` birinchi ishga tushishda sxemani qo‘llaydi. Avvaldan foydalanuvchilar bo‘lsa, mavjud texnik administrator bilan kiring.
4. Techadmin bilan **Tarozi va boshqaruv → Foydalanuvchilar** orqali har xodimga alohida login bering. `parkovka_tashqi` tashqi parkovka, `kpp2` O‘zbekiston darvozasi, `ayritom` post tomondagi darvoza, `operator` tarozi uchun. `admin` umumiy hisobotlarni ko‘radi. Barcha narxlar **Sozlamalar → Markaziy tariflar** bo‘limida boshqariladi.
5. GitHub Actions’dagi **Airitom checks** vaqtinchalik PostgreSQL bazada kirish, ruxsatlar, navbatni ko‘chirish, to‘lov, qayta yuborish va Excel eksportini tekshiradi. Render’da quyidagi Gunicorn start buyrug‘i Blueprint ichida berilgan.

## Hisoblash va harakat tartibi

- Bir raqamga bir vaqtda bitta ochiq tashrif. Tashrif IDsi o‘zgarmas navbat raqami. Xodimlar ro‘yxatni kelish tartibida ko‘radi, KPP2 kiritilayotgan raqam uchun aynan Free Zone navbatini tekshiradi.
- Ish kuni **Asia/Tashkent kalendar sanasi** bilan hisoblanadi. Kirish kuni birinchi kun. Masalan, 23:55 da kirib ertasi 00:05 da chiqilsa ikki kun. Dastur ichidagi boshlang‘ich tarif 30 000 so‘m. Tarif o‘zgarsa, ochiq tashrif avvalgi tarifini saqlaydi. Techadmin istasa yangi summani ochiq tashriflarga ham bir amal bilan qo‘llaydi.
- Bojxona navbati: parkovkada bir kunlik to‘lov → parkovkadan chiqishda qolgan qarz → tashrif yopiladi. Free Zone navbati: parkovkada bir kunlik to‘lov → KPP2 orqali hududga o‘tish → ikki darvozadan birida yakuniy hisob. Parkovka xodimi chiqishni alohida qayd etsa, mashina KPP2 yo‘lida ko‘rinadi. KPP2 parkovkadan bevosita o‘tishni ham qayd eta oladi. O‘tishda qayta kirish to‘lovi olinmaydi.
- Ayritom tomondan hududga bevosita kirishda bir kunlik to‘lov olinadi. Hududdan chiqishda barcha qamrab olingan kunlar summasidan shu tashrifning oldingi to‘lovlari ayriladi. Qarz bo‘lmasa, chiqish 0 so‘mlik chek bilan qayd etiladi.
- To‘lov va harakat bitta tranzaksiyada saqlanadi. Xodim summa va to‘lov usulini tasdiqlaydi. Qayta yuborilgan tasdiq o‘sha chekni qaytaradi. Bir vaqtda boshqa xodim tashrifni o‘zgartirsa, hisobni qaytadan ko‘rish talab etiladi.
- Darvoza cheki brauzerdan chop etiladi. Printerda 80 mm qog‘oz, 100% masshtab va sarlavhasiz chop rejimini tanlang. QR matni telefonning o‘zida ochiladi. Xodim so‘nggi cheklar ro‘yxatidan qayta chop eta oladi. Tarozining Windows agenti o‘z ish tartibini saqlaydi.
- Umumiy panelda joylashuv hozirgi vaqtga, tushum esa **pul qabul qilingan vaqtga** tegishli. Hafta dushanbadan, oy va yil birinchi kunidan bugungacha olinadi. Tarozi hisobotga o‘zining barcha to‘langan xizmatlarini kiritadi. Ko‘chish va qayta chop tushumni oshirmaydi. Tarozi operatori parkovkada undirilgan shu kunlik haqni qo‘shimcha xizmat deb ikkinchi marta kiritmasligi kerak.
- Texnik SQLite backupga darvoza tashriflari va hodisalari ham yoziladi. Tarozi oynasidagi eski SQLite import faqat tarozi ma’lumotlarini import qiladi. **Barcha jadvallarni birga tiklash uchun PostgreSQL bazasining to‘liq zaxirasini saqlang.**

## Tarozi moduli va Windows agenti

AIRITOM LOGISTICS CENTER MCHJ uchun qayta yozilgan tarozi terminali. Web qismi GitHub → Render orqali ishlaydi, doimiy ma'lumotlar Supabase PostgreSQL bazasida saqlanadi. Windows lokal agenti termal printerga dialogsız ESC/POS chek yuboradi va har kuni Supabase ma'lumotlarini kompyuterdagi tanlangan papkaga SQLite `.db` backup qiladi.

## Asosiy imkoniyatlar

- Barcha sahifalardan oldin majburiy login.
- `operator`, `admin`, `techadmin` rollari.
- Operator loginidan keyin to'g'ridan-to'g'ri kiosk: vaznni kilogrammda qo'lda kiritish, xizmatlarni tanlash, to'lovni tasdiqlash va chek chiqarish. Operator bugungi hamda kechagi operatsiyalarni qidirib, cheklarini qayta ko'ra oladi.
- Admin loginidan keyin alohida admin dashboard: hisobot, qidiruv, tarix, CSV/Excel eksport va eski SQLite import.
- Techadmin loginidan keyin alohida texnik monitoring dashboardi: narxni o'zgartirish, foydalanuvchi yaratish/bloklash, parolni reset qilish, qo'lda backup, operatsion bazani himoyalangan tarzda tozalash va tizim monitoringi.
- Operator, admin va techadmin jadvallarida server tomondagi qidiruv hamda 10/15/20/50/100 talik sahifalash mavjud.
- Ochiq qurilmalar Supabase'dagi o'zgarish versiyasini yengil so'rov bilan kuzatadi; faqat o'zgarish bo'lganda ko'rinayotgan ma'lumot yangilanadi va kiritilayotgan matn saqlanadi.
- Tarozi, parkovka/Free Zone kunlik haqi, hududga kirish va qayta yuklash narxlarini faqat techadmin belgilaydi. Operator xizmatni tanlaydi, summa serverdagi faol tarifdan avtomatik olinadi. O'chirilgan tarif operator oynasida ko'rinmaydi.
- Chekda avtomobil vazni, har bir tanlangan xizmat va yakuniy summa alohida ko'rsatiladi. QR kod saytga yo'naltirmaydi: skaner qilinganda mashina, vazn, vaqt va barcha to'lovlar telefonning o'zida oddiy matn sifatida ko'rinadi.
- Sessiya bir qurilmada yangilanib turadi va faqat foydalanuvchi chiqish tugmasini bosganda yopiladi.
- Parollar `scrypt` bilan xeshlanadi. Amaldagi parolni ko'rib bo'lmaydi; techadmin faqat yangi parol o'rnatadi.
- To'langan yozuv o'chirilmaydi. Kutilayotgan operatsiya bekor qilinsa `cancelled` holatiga o'tadi.
- SQLite import takroriy yozuvlarni fingerprint orqali o'tkazib yuboradi.
- Lokal agent bor bo'lsa termal printerga RAW ESC/POS chek yuboriladi; agent bo'lmasa brauzerning 80 mm print oynasi ochiladi.
- Supabase xotirasi, DB kechikishi, ulanishlar, Render RAM/CPU/uptime, so'rov tezligi va 14 kunlik faollik grafigi.

## Loyiha tuzilishi

```text
app.py                       Flask server va barcha API
schema.sql                   Supabase/PostgreSQL sxemasi
templates/                   HTML sahifalar
static/                      CSS, JavaScript, rasm va ikonka
local_agent.py               Windows printer + backup agenti
install_local_agent.ps1      Windows Task Scheduler o'rnatuvchisi
render.yaml                  Render Blueprint
requirements.txt             Python paketlari
Procfile                     Render/Gunicorn start buyrug'i
```

Eski distributiv va dizayn manbalari olib tashlangan; import uchun kerak bo'lgan asl baza `.legacy-backup/tarozi.db` sifatida saqlangan. `_internal`, `.legacy-backup`, lokal `.db`, `.env` va agent konfiguratsiyasi `.gitignore` sabab GitHub'ga chiqmaydi.

## 1. Supabase tayyorlash

1. Supabase'da yangi project yarating.
2. Project ichida **Connect** tugmasini oching.
3. Render IPv4 tarmog'i uchun **Session pooler** ulanish satrini tanlang (odatda port `5432`).
4. Parolda `@`, `:`, `/`, `#` kabi belgilar bo'lsa ularni URL-encode qiling.
5. Ulanish satri oxirida `sslmode=require` bo'lsin.

Misol:

```text
postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require
```

Jadvallarni qo'lda yaratish shart emas: birinchi server so'rovida `schema.sql` xavfsiz ravishda bajariladi.

## 2. GitHub'ga joylash

GitHub'da bo'sh repository yarating, keyin shu papkada:

```powershell
git init
git add .
git commit -m "Tarozi Kiosk 2.6 Pro"
git branch -M main
git remote add origin https://github.com/USERNAME/REPOSITORY.git
git push -u origin main
```

`USERNAME/REPOSITORY` o'rniga o'zingizning repository manzilingizni yozing. Pushdan oldin `git status`da `.env`, `*.db`, `.legacy-backup`, `_internal` va `local_agent_config.json` yo'qligini tekshiring.

## 3. Render orqali deploy

1. Render dashboard → **New** → **Blueprint**.
2. GitHub repository'ni ulang.
3. Render `render.yaml`ni o'qiydi va web service yaratadi.
4. Quyidagi maxfiy qiymatlarni kiriting:

| Environment variable | Qiymat |
|---|---|
| `DATABASE_URL` | Supabase Session pooler URI |
| `SECRET_KEY` | Blueprint birinchi deployda avtomatik yaratadi |
| `INITIAL_TECHADMIN_PASSWORD` | Ixtiyoriy birinchi parol; bo‘sh bo‘lsa Render logida vaqtinchalik parol yaratiladi |

Qolgan muhim qiymatlar `render.yaml`da tayyor:

- `INITIAL_TECHADMIN_USERNAME=techadmin`
- `APP_TIMEZONE=Asia/Tashkent`
- `COOKIE_SECURE=true`
- `LOCAL_PRINTER_AGENT_ENABLED=true` — `AgentSetup.exe` o'rnatilgan kompyuterlarda dialogsız chop; agent yo'q bo'lsa brauzer rejimi ishlaydi.
- `SUPABASE_DB_LIMIT_BYTES=524288000`

`SUPABASE_DB_LIMIT_BYTES` monitoringdagi “limit/qolgan joy” hisobiga ishlatiladi. Supabase tarifingizdagi real limit boshqacha bo'lsa shu qiymatni baytlarda almashtiring. Haqiqiy ishlatilgan hajm bevosita PostgreSQL `pg_database_size`dan olinadi.

Render servisini qo'lda yaratgan bo'lsangiz, **Settings → Start Command** qiymati aynan quyidagicha bo'lsin:

```text
gunicorn app:app --workers 1 --threads 4 --timeout 120 --access-logfile -
```

`SECRET_KEY`ni keyingi deploylarda almashtirmang. Uni almashtirish barcha mavjud login sessiyalarini bir marta bekor qiladi.

Deploy tugagach `/health` manzili `{"status":"ok"}` qaytarishi kerak. Birinchi kirish:

```text
Login: techadmin
Parol: Render'dagi INITIAL_TECHADMIN_PASSWORD qiymati yoki logdagi AIRITOM FIRST LOGIN vaqtinchalik paroli
```

## 4. Eski `tarozi.db`ni import qilish

1. Techadmin yoki admin bilan kiring.
2. **Import va Backup** bo'limini oching.
3. `.db`, `.sqlite` yoki `.sqlite3` faylni tanlang.
4. **Importni boshlash** tugmasini bosing. Yuklash foizi va keyingi Supabase'ga yozish bosqichi ekranda ko'rinadi.

Import quyidagilarni qiladi:

- eski `weighings`, yangi backup `weighings` yoki parkovka dasturining `vehicles` jadvalini avtomatik taniydi;
- faylni faqat o'qish rejimida ochadi;
- sanalar, avtomobil raqami, tarif va holatni ko'chiradi;
- `vehicles` formatida parking/navbat puli “hududga kirish”, `reload_fee` esa “qayta yuklash” sifatida saqlanadi; mavjud bo'lmagan vazn `0 kg` bo'ladi;
- eski admin/login parollarini ko'chirmaydi;
- katta faylni 1000 qatorli bo'laklarda yozadi;
- qayta import qilinganda takroriy yozuvlarni tashlab ketadi.

Maksimal upload hajmi 256 MB.

## 5. Qo'lda backup

Operator asosiy oynasidagi **Backup olish** tugmasini bosadi. Chrome yoki Edge papka tanlash oynasini ochadi va tayyor `.db` fayl aynan tanlangan papkaga yoziladi. Brauzer papka tanlash API'sini qo'llamasa, fayl odatiy **Downloads** papkasiga yuklanadi. Bu amal uchun token kerak emas.

Operator backupida parol xeshlari bo'lmaydi, ammo barcha tarozi va to'lov ma'lumotlari hamda xizmat sozlamalari bo'ladi. Shu faylni keyin **Import va Backup** bo'limi orqali qayta import qilish mumkin.

Techadmin → **Import va Backup** → **Zaxira Nusxa (.db)** orqali foydalanuvchi xeshlari bilan to'liq server backupini ham olishi mumkin.

Server ayni paytdagi foydalanuvchilar (faqat xavfsiz xeshlar), sozlamalar va barcha operatsiyalarni portable SQLite faylga yozib yuklatadi.

Windows fayl nomida `:` belgisi mumkin emas. Shu sabab fayl nomi quyidagicha bo'ladi:

```text
01.10.2026 00-00 holatiga backup.db
```

## 6. Windows lokal agentini bitta EXE bilan o'rnatish

GitHub repositoryga kod yuborilgach **Actions → Build Windows Agent → Run workflow** ni bosing.
Ish tugagach `AgentSetup-windows` artifactini yuklab, ichidan `AgentSetup.exe`ni oling.
Printer ulangan kompyuterda shu bitta faylni oching:

1. Render sayt manzili tayyor holda turadi.
2. Ro'yxatdan kerakli Windows printerni tanlang.
3. Odatda chop usuli `windows`; faqat haqiqiy ESC/POS printerda `escpos` tanlanadi.
4. Backup kerak bo'lsa papka va techadmin bergan tokenni kiriting; chop uchun token shart emas.
5. **O'RNATISH** tugmasini bosing.

Agent `%LOCALAPPDATA%\TaroziKioskAgent\TaroziPrinterAgent.exe` manziliga o'rnatiladi,
joriy Windows foydalanuvchisi tizimga kirganda avtomatik ishga tushadi va `127.0.0.1:17832`da faqat
sozlangan sayt so'rovlarini qabul qiladi. Python o'rnatish shart emas. Yangilangan EXE kodi
`build_agent.ps1` orqali ham qo'lda yig'ilishi mumkin.

Render Environment bo'limida `LOCAL_PRINTER_AGENT_ENABLED=true` bo'lishi shart. Muvaffaqiyatli agent chopida
brauzerning chop oynasi ochilmaydi. Agent ishlamasa chekni yo'qotmaslik uchun brauzer chop oynasi zaxira yo'l sifatida ochiladi.

Windows SmartScreen imzosiz, yangi yig'ilgan EXE uchun ogohlantirish berishi mumkin. Bu kod imzolash sertifikati
yo'qligi sababli; EXE aynan o'zingizning GitHub Actions jarayoningizda shu repository kodidan yig'iladi.

### Eski PowerShell usuli

Bu qism sayt ochiladigan va termal printer ulangan kompyuterda bir marta bajariladi.

Talablar:

- Windows 10/11;
- Python o'rnatilgan va `python` PATH'da;
- printer Windows'da o'rnatilgan; imkon qadar default printer qiling;
- agent fayllari doimiy papkada turishi kerak.

Techadmin → **Sozlamalar** → **Avtomatik tungi backup agenti (ixtiyoriy)** bo'limini ochib token yarating va darhol nusxalang. Token keyin qayta ko'rsatilmaydi. Bu token faqat avtomatik agent uchun; saytdagi oddiy backup tugmasiga kerak emas.

PowerShell'ni oching. O'rnatuvchi `pywin32`, `Pillow` va `qrcode` printer kutubxonalarini o'zi o'rnatadi:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_local_agent.ps1
```

O'rnatuvchi quyidagilarni so'raydi:

- Render sayt URL'i, masalan `https://tarozi-kiosk.onrender.com`;
- ruxsat etilgan brauzer origin — odatda sayt URL'ining aynan o'zi;
- techadmin yaratgan backup token;
- backup saqlanadigan lokal papka;
- printer nomi (bo'sh qoldirilsa Windows default printer).
- chop etish rejimi: odatda `windows`; faqat haqiqiy ESC/POS printer uchun `escpos`.

U ikki vazifa yaratadi:

- `TaroziKiosk Local Print Agent` — Windows'ga kirishda ishga tushadi;
- `TaroziKiosk Daily Backup` — har kuni 00:05 da ishlaydi, kompyuter o'chiq bo'lsa keyingi mavjud vaqtda ishga tushadi.

Qo'lda tekshirish:

```powershell
python local_agent.py test-print
python local_agent.py backup
```

## 7. Avtomatik backup qanday ishlaydi

Agent vaqtinchalik `.part` faylga yuklaydi, SQLite sarlavhasi va `PRAGMA quick_check`ni tekshiradi, keyin atomik almashtiradi. Yangi backup to'liq va sog'lom bo'lmasa oldingi fayl o'chirilmaydi.

Muvaffaqiyatli yangi backupdan so'ng shu papkadagi oldingi `* holatiga backup.db` o'chiriladi. Natijada papkada faqat eng yangi backup qoladi.

## 8. Chek chiqarish

2026-10-02 yangilanishi: matn, to'lovlar va QR `receipt_render.py` orqali **bitta PNG**ga chiziladi.
Brauzer va Windows agenti shu chizmadan foydalanadi; printer matn shriftlarini alohida ishlamaydi.
80 mm sahifada 72 mm bosma tasvir va 4 mm chetlar mavjud. PDF nusxa ham shu tasvir bilan yaratiladi.
Chek balandligi serverda hisoblanadi, sahifa uslubi CSP nonce bilan ruxsat oladi.
Browser print sozlamasida 80 mm printer qog'ozi, 100% masshtab va headers/footers o'chirilgan bo'lishi kerak.
Qog'oz uzunligini fizik printer drayveri ham qo'llashi kerak; Windows/ESC-POS rejimi uchun agentni yangilang.

Chop holatlari: so'ralgan, dialog yopilgan (natija noma'lum), printer navbatiga yuborilgan,
operator tasdiqlagan yoki chiqmagan. `afterprint` hodisasi qog'oz chiqqanini isbotlamaydi.
Tasdiqlash tugmalari brauzer cheki tagida turadi. Qayta chop hisoboti — urinishlar hisoboti.
Manbalar: https://developer.mozilla.org/en-US/docs/Web/API/Window/afterprint_event
va ESC/POS raster protokoli: https://download4.epson.biz/sec_pubs/pos/reference_en/escpos/gs_lv_0.html

To'lov tasdiqlanganda:

1. Standart holatda chek brauzer orqali chiqariladi va `127.0.0.1`ga keraksiz so'rov yuborilmaydi.
2. To'liq 80 mm chek ko'rinishi sayt ichidagi bloklanmaydigan preview oynasida ochiladi.
3. Agent bo'lsa standart `windows` rejimida chek QR bilan bitmapga aylantirilib Windows printer drayveri orqali dialogsız yuboriladi. `escpos` rejimi faqat ESC/POS tilini tushunadigan printerlar uchun.
4. Agent yoki printer mavjud bo'lmasa preview ichidan brauzer chop etish dialogi avtomatik ochiladi. Preview'dagi **Chekni Chop Etish** tugmasi bilan qayta urinish mumkin.
5. Admin qidiruv/tarix bo'limidan to'langan chekni istalgan vaqtda qayta chiqarishi mumkin.

Printer qog'ozini surish tugmasi ham lokal agent orqali ishlaydi.
Dialogsız Windows printer rejimi kerak bo'lsa lokal agentni o'rnatib, Render'da `LOCAL_PRINTER_AGENT_ENABLED=true` qiling.

## Operator qulayliklari va yangi hisobotlar

- F2: yangi o'lchash yoki tugallanmagan operatsiyaga qaytish. Enter: keyingi maydon; to'lov tasdiqlangan bo'lsa saqlash. Esc: oynani yopish/formadan chiqish.
- Qo'shimcha xizmat summalari markaziy tarifdan avtomatik olinadi va operator tomonidan o'zgartirilmaydi.
- Davlat raqami katta harfga va tanilgan O'zbekiston formatiga avtomatik keltiriladi.
- Yashil to'lov tasdig'i, oxirgi chek tugmasi, operatorning naqd/karta/hisob-raqam tushumi.
- Forma har foydalanuvchi va brauzer tabiga alohida sessionStorage'da saqlanadi. Sahifa yangilanganda serverdagi operatsiya tekshirilib tiklanadi; bu offline to'lov emas.
- Takroriy yaratish so'rovi noyob request ID bilan qayta chek yaratmaydi. To'lov va bekor qilish bazada blokirovka bilan bajariladi.
- **Tahlil va eksport**: soat, kun, hafta, oy, operator, to'lov usuli, xizmat, avtomobil, bekor qilingan chek va qayta chop urinishlari.
- Operator/sana/to'lov filtrlari, 10–100 qatorli sahifalar. Daromad faqat to'langan operatsiyalardan; davr operatsiya yaratilgan sanasi bo'yicha.
- Haqiqiy Excel `.xlsx` (summalar son), shriftlari ichiga joylangan PDF. Eksport butun filtr natijasini oladi. Render xotirasini himoyalash uchun Excel 20 000, PDF 2 000 qator bilan cheklangan; oshsa davrni toraytirish so'raladi, natija yashirincha kesilmaydi.
- Kunlik kassa dalolatnomasi: bitta kun, operator, naqd/karta/bank, haqiqiy naqd, farq va imzo joylari. Bu yaratilgan paytdagi PDF; smenani bloklab yopish amali emas.

Deploy uchun `requirements.txt`, `schema.sql`, `app.py`, yangi `receipt_render.py`, `reports.py`, `static/` va `templates/`ni birga yangilang. `schema.sql` qo'shimcha ustun/jadvallarni o'zi yaratadi; mavjud cheklar o'chirilmaydi. Tarixiy cheklar bo'yicha oldingi chop holati noma'lum, yangi urinishlar yangilanishdan boshlab qayd etiladi.
Lokal agent ishlatilsa `local_agent.py`, `receipt_render.py`, `install_local_agent.ps1`ni ham yangilab, o'rnatuvchini qayta ishga tushiring.

## Rollar

| Funksiya | Operator | Admin | Techadmin |
|---|:---:|:---:|:---:|
| Kiosk va chek | ✓ | — | — |
| Bugun/kecha jurnali va cheklarni ko'rish | ✓ | — | — |
| Tokensiz `.db` backup | ✓ | ✓ | ✓ |
| O'zining dashboardi/hisobot/qidiruv | — | ✓ | ✓ |
| SQLite import | — | ✓ | ✓ |
| Qo'lda to'liq backup | — | — | ✓ |
| Narxni o'zgartirish | — | — | ✓ |
| Login/parol/rollarni boshqarish | — | — | ✓ |
| Supabase/Render monitoring | — | — | ✓ |
| Backup tokenlari | — | — | ✓ |
| Operatsion bazani tozalash | — | — | ✓ |

## Muhim eslatmalar

- Render web serveri foydalanuvchining Windows papkasiga bevosita yozolmaydi. Shu sabab avtomatik lokal backup va dialogsız printer uchun `local_agent.py` zarur.
- Render'ning vaqtinchalik diski permanent ma'lumot saqlash uchun ishlatilmaydi; asosiy ma'lumot Supabase'da.
- Render Free service bo'sh turganda uxlab qolishi mumkin; birinchi so'rov sekinroq ochilishi ehtimoli bor.
- `SECRET_KEY`, `DATABASE_URL`, boshlang'ich parol va backup tokenini GitHub'ga yozmang.
- `local_agent_config.json` maxfiy token saqlaydi va `.gitignore`ga kiritilgan.
- Render Free xotirasini tejash uchun loyiha bitta Gunicorn worker va to'rtta threadda ishlaydi.

## Loglar

- Render: service → **Logs**.
- Windows agent: loyiha yonidagi `local_agent.log`.
- Import xatosida Render logida traceback bo'ladi, foydalanuvchiga esa xavfsiz qisqa xabar ko'rsatiladi.
