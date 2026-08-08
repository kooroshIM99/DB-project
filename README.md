# ArXiv Full-Text Search with Elasticsearch

این مخزن یک سامانهٔ کامل جست‌وجوی تمام‌متن روی نمونه‌ای شامل ۵۰٬۰۰۰
مقالهٔ arXiv است. مسیر پروژه از پاک‌سازی JSONL تا index کردن، keyword/contain/fuzzy
search، معماری ترکیبی weighted-RRF، سنجش کیفیت، load test چندکاربره و مقایسهٔ
baseline/optimized را پوشش می‌دهد.

نتایج نهایی و تحلیل کامل در [گزارش نهایی](reports/final-report.md) قرار دارند.

## طراحی و تصمیم تکنولوژی

Elasticsearch برای این پروژه انتخاب شد، چون query scoring، phrase search،
fuzziness، filter context، aggregation و analyzerهای قابل تنظیم را در یک موتور
جست‌وجوی متنی فراهم می‌کند. PostgreSQL Full-Text Search برای داده‌های رابطه‌ای
گزینهٔ قوی‌ای است، اما برای آزمایش‌های analyzer، n-gram، fuzzy search و فشار این
پروژه به تنظیم و کد جانبی بیشتری نیاز داشت.

دو index اصلی کاملاً جدا هستند:

- `arxiv_papers_baseline`: mapping ساده با standard analyzer
- `arxiv_papers_optimized`: mapping مرحلهٔ ۹ با قابلیت n-gram و تنظیمات بهینه

هر دو index از یک دیتاست، batch=500، یک shard، صفر replica و محدودیت منابع یکسان
استفاده می‌کنند.

## پیش‌نیازها

- Git و Git LFS
- Python 3.11.8
- Docker 28.3.2 یا سازگار
- Docker Compose 2.38.2 یا سازگار
- حداقل 4GB حافظه برای Docker Desktop
- حدود 1GB فضای آزاد برای dataset، indexها و artifactها

نسخه‌های Python packageها در `requirements.txt` دقیقاً pin شده‌اند.

## دریافت و نصب

```bash
git clone https://github.com/kooroshIM99/DB-project.git
cd DB-project
git lfs pull

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

در Windows PowerShell، فعال‌سازی محیط با
`.venv\Scripts\Activate.ps1` انجام می‌شود.

## منبع و قرارداد دیتاست

منبع بالادستی متادیتا:
[Cornell University arXiv Dataset on Kaggle](https://www.kaggle.com/datasets/Cornell-University/arxiv).

فایل خام این پروژه یک نمونهٔ محلی ۵۰٬۰۰۰ رکوردی است. مخزن اولیه seed یا اسکریپت
نمونه‌گیری را ارائه نکرده؛ بنابراین URL بالادستی و hash فایل تحویلی ثبت شده‌اند،
اما روش استخراج دقیق نمونه یک محدودیت provenance است.

| فایل | اندازه | SHA-256 |
|---|---:|---|
| `dataset/arxiv_project_sample_50k.jsonl` | 141,586,787 | `190b0a678f946338d9daa100a193e8b9ea59c56cf14157f288ee77b05cf03f84` |
| `dataset/arxiv_project_sample_50k_cleaned.jsonl` | 141,586,775 | `86b9febd7fc85d1b9c97377b36db525391854c134430b85e30c87a1dc18f2ad6` |

جزئیات schema و پاک‌سازی در [dataset/DATASET.md](dataset/DATASET.md) آمده است.
فایل خام را تغییر ندهید.

اعتبارسنجی نسخهٔ پاک‌شده:

```bash
python scripts/validate_cleaned_dataset.py \
  dataset/arxiv_project_sample_50k_cleaned.jsonl
```

برای بازتولید پاک‌سازی بدون overwrite کردن artifact اصلی:

```bash
python scripts/clean_dataset.py \
  --input dataset/arxiv_project_sample_50k.jsonl \
  --output /tmp/arxiv_project_sample_50k_cleaned.jsonl \
  --report /tmp/arxiv_cleaning_report.json

shasum -a 256 /tmp/arxiv_project_sample_50k_cleaned.jsonl
```

hash خروجی باید با hash پاک‌شدهٔ جدول بالا برابر باشد.

## اجرای Elasticsearch

```bash
docker compose up -d elasticsearch
docker compose ps
python scripts/check_elasticsearch.py
```

تنظیمات ثابت محیط:

- Elasticsearch 9.4.4
- single node
- endpoint: `http://127.0.0.1:9200`
- 2 CPU
- container memory: 2GB
- JVM heap: 1GB
- یک shard و صفر replica برای indexهای اصلی
- security غیرفعال، فقط برای محیط محلی

جزئیات بیشتر در [elasticsearch/README.md](elasticsearch/README.md) ثبت شده است.

## ساخت و ورود داده

ساخت baseline:

```bash
python scripts/create_index.py \
  --index arxiv_papers_baseline \
  --mapping elasticsearch/mappings/arxiv_papers_baseline.json

python scripts/index_dataset.py \
  --dataset dataset/arxiv_project_sample_50k_cleaned.jsonl \
  --batch-size 500 \
  --report results/ingestion_baseline.json
```

ساخت optimized:

```bash
python scripts/create_index.py \
  --index arxiv_papers_optimized \
  --mapping elasticsearch/mappings/arxiv_papers_optimized.json

python scripts/index_optimized.py \
  --dataset dataset/arxiv_project_sample_50k_cleaned.jsonl \
  --batch-size 500 \
  --report results/ingestion_optimized.json
```

اسکریپت ساخت، index موجود را به‌طور پیش‌فرض حذف نمی‌کند. گزینهٔ `--recreate`
حذف و بازسازی می‌کند و فقط هنگام reset آگاهانه باید استفاده شود.

## Smoke test تحویل

بعد از ingestion، این دستور اتصال، نسخه، cluster health، وجود هر دو index، count
برابر ۵۰٬۰۰۰ و اجرای query واقعی را بررسی می‌کند:

```bash
python scripts/smoke_test.py
```

خروجی ماشین‌خوان در `results/smoke_test.json` ذخیره می‌شود.

## اجرای جست‌وجوهای اصلی

اجرای تعاملی یک query baseline:

```bash
python scripts/search_queries.py \
  --query-id keyword_query_optimization
```

اجرای یک query optimized:

```bash
python scripts/search_queries.py \
  --queries queries/search_queries_optimized.json \
  --execution optimized \
  --index arxiv_papers_optimized \
  --query-id keyword_query_optimization
```

فایل‌های query، نیت منطقی و DSL اجرایی را جدا و قابل hash نگه می‌دارند.

## Benchmark تک‌کاربره

Baseline:

```bash
python scripts/benchmark.py
```

Optimized:

```bash
python scripts/benchmark.py \
  --queries queries/search_queries_optimized.json \
  --mapping elasticsearch/mappings/arxiv_papers_optimized.json \
  --ingestion-report results/ingestion_optimized.json \
  --execution optimized \
  --index arxiv_papers_optimized \
  --json-report results/search_optimized.json \
  --csv-report results/search_optimized.csv \
  --chart results/search_optimized_latency.png
```

هر query پنج warm-up و ۳۰ iteration اندازه‌گیری‌شده دارد. latency به‌صورت
end-to-end سمت client و P95 با nearest-rank محاسبه می‌شود. seed ثابت
`20250808` است.

## معماری ترکیبی و کیفیت

Baseline:

```bash
python scripts/hybrid_search.py
python scripts/evaluate_relevance.py
```

Optimized:

```bash
python scripts/hybrid_search.py \
  --index arxiv_papers_optimized \
  --contract queries/hybrid_comparison_queries_optimized.json \
  --mapping elasticsearch/mappings/arxiv_papers_optimized.json \
  --ingestion-report results/ingestion_optimized.json \
  --json-report results/hybrid_comparison_optimized.json \
  --performance-csv results/hybrid_performance_optimized.csv \
  --judgment-template results/relevance_judgments_optimized_template.csv

python scripts/reuse_relevance_judgments.py

python scripts/evaluate_relevance.py \
  --comparison results/hybrid_comparison_optimized.json \
  --judgments results/relevance_judgments_optimized.csv \
  --quality-csv results/hybrid_quality_optimized.csv \
  --quality-json results/hybrid_quality_optimized.json
```

در اجرای تازه، قضاوت دستی باید قبل از `evaluate_relevance.py` کامل شود.
`reuse_relevance_judgments.py` فقط وقتی موفق می‌شود که تمام زوج‌های
`(query_id, paper_id)` قبلاً قضاوت شده باشند.

معماری hybrid از weighted RRF با `k=60` و وزن‌های keyword=1.0،
contain=0.9 و fuzzy=0.65 استفاده می‌کند. fuzzy فقط در صورت کمبود candidate،
نبود overlap در top-10 یا query اجباری typo اجرا می‌شود.

## Load test چندکاربره

هر اجرای کامل تقریباً ۱۲ دقیقه طول می‌کشد: ده سناریو، هرکدام ۱۰ ثانیه warm-up
و حداقل ۶۰ ثانیه measurement.

Baseline:

```bash
python scripts/run_load_tests.py
```

Optimized:

```bash
python scripts/run_load_tests.py \
  --contract queries/load_test_scenarios_optimized.json \
  --base-queries queries/search_queries_optimized.json \
  --hybrid-queries queries/hybrid_comparison_queries_optimized.json \
  --mapping elasticsearch/mappings/arxiv_papers_optimized.json \
  --ingestion-report results/ingestion_optimized.json \
  --json-report results/load_test_optimized.json \
  --measurements results/load_test_optimized_measurements.jsonl.gz \
  --csv-report results/load_test_optimized.csv \
  --latency-chart results/load_test_optimized_latency.png \
  --throughput-chart results/load_test_optimized_throughput.png
```

مدل بار closed-loop بدون think time است. هر client اتصال HTTP پایدار و PRNG
مستقل با seed ثبت‌شده دارد. شروع measurement با barrier هم‌زمان می‌شود.

## Ablation، مقایسه و گزارش نهایی

```bash
python scripts/run_stage9_ablations.py
python scripts/compare_stage9.py
python scripts/plot_metrics.py
python scripts/build_reproducibility_manifest.py
python scripts/generate_final_report.py
```

خروجی‌های نهایی:

- `reports/final-report.md`: گزارش تحویل
- `results/reproducibility_manifest.json`: hashها، نسخه‌ها و ممیزی traceability
- `results/metrics_summary.csv`: جدول نرمال‌شدهٔ تمام سناریوها
- `results/stage9_comparison.csv`: مقایسهٔ baseline/optimized
- `results/metrics_dashboard.png`: dashboard متریک‌ها
- `results/stage9_search_before_after.png`: نمودار تک‌کاربره
- `results/stage9_load_before_after.png`: نمودار بار

## نتایج تأییدشده

- ۵۰٬۰۰۰ سند در هر index
- تمام ۹ query تک‌کاربره با count برابر و latency کمتر در optimized
- تمام ۱۲ حالت query/method معماری ترکیبی اجراشده
- ۱۰۱ قضاوت کیفیت؛ Precision@10 در همهٔ حالت‌ها بدون افت
- ۱۰ سناریوی load روی هر نسخه، صفر خطا
- baseline load: 836,652 درخواست اندازه‌گیری‌شده
- optimized load: 984,503 درخواست اندازه‌گیری‌شده
- n-gram در ablation زیررشته ۷۲٪ تا ۷۵٪ latency کمتر
- trade-off اندازه: حدود 103MB baseline در برابر 366MB optimized در snapshot مقایسه

## تست‌ها

```bash
python -m pytest -q
python -m compileall -q scripts tests
```

تست‌ها پاک‌سازی، mapping، ingestion batch، query contract، aggregation،
محاسبهٔ P95/throughput، RRF، trigger fuzzy، کیفیت، load protocol، artifact
hydration و smoke test را پوشش می‌دهند.

## ممیزی artifactها

```bash
python scripts/build_reproducibility_manifest.py
```

این دستور وجود و SHA-256 فایل‌های ضروری را ثبت می‌کند و بررسی می‌کند هر شش
benchmark اصلی نام index، mapping/query/dataset hash، seed، نسخه، shard/replica
و محدودیت منابع لازم را داشته باشد.

## توقف و پاک‌سازی

توقف بدون حذف indexها:

```bash
docker compose down
```

حذف کامل volume و تمام indexهای محلی عملیاتی مخرب است:

```bash
docker compose down -v
```

فقط زمانی از `-v` استفاده کنید که قصد بازسازی کامل داده‌های Elasticsearch را
دارید. فایل‌های JSONL و artifactهای داخل مخزن با این دستور حذف نمی‌شوند.
