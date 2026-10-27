# AMIGA Robot Admin

**AMIGA Robot Admin** adalah aplikasi web untuk membantu alur pemrosesan invoice vendor menjadi voucher dengan dukungan AI. Repository ini terdiri dari frontend React dan backend FastAPI.

> Status: versi awal (`0.1.0`). Konfigurasi layanan eksternal dan database perlu disiapkan sesuai environment masing-masing.

## Fitur utama

- **Invoice processing** — mendukung alur ekstraksi dan pemrosesan data invoice.
- **Voucher management** — mendukung alur invoice-to-voucher.
- **AI chat** — antarmuka percakapan untuk membantu workflow.
- **Workspace dan refinement** — modul untuk pengelolaan workspace serta penyempurnaan hasil.
- **Client, user, request, dan admin management** — endpoint backend untuk modul-modul administrasi.
- **PDF generation** — backend menyertakan layanan rendering PDF.
- **Background workers** — worker untuk ekstraksi dokumen dan indexing dijalankan saat aplikasi backend dimulai.

Ketersediaan detail dan akses setiap fitur bergantung pada konfigurasi aplikasi.

## Teknologi

| Bagian | Teknologi |
|---|---|
| Frontend | React 18, TypeScript, Vite |
| Navigasi | React Router |
| PDF di frontend | `react-pdf` |
| Backend API | Python, FastAPI, Uvicorn |
| Database | SQLAlchemy Async, PostgreSQL melalui `asyncpg` |
| Database migration | Alembic |
| AI / cloud integration | AWS SDK (`boto3`) |
| Queue dan worker | RQ, Redis |
| PDF dan dokumen | ReportLab, WeasyPrint, pypdf, Pillow |
| Vector search support | `pgvector` |

## Struktur repository

```text
amiga_robot_admin/
├── backend/
│   ├── app/
│   │   ├── main.py              # Inisialisasi FastAPI, middleware, router, worker
│   │   ├── routes/              # Endpoint API berdasarkan modul
│   │   └── services/            # Layanan bisnis dan background worker
│   └── requirements.txt         # Dependensi Python
├── frontend/
│   ├── src/                     # Source code React
│   ├── package.json             # Script dan dependensi frontend
│   └── ...
├── test_invoices/                # Berkas invoice untuk pengujian
├── test_bedrock.py               # Skrip pengujian terkait Bedrock
└── .kiro/specs/invoice-to-voucher/ # Spesifikasi workflow
```

## Persyaratan

Pastikan perangkat pengembangan memiliki:

- Python yang kompatibel dengan dependensi backend.
- Node.js dan npm.
- PostgreSQL.
- Redis.
- AWS credentials dan izin layanan yang dibutuhkan apabila fitur AWS/Bedrock digunakan.

Versi Python yang tepat tidak didefinisikan di bagian ini; sesuaikan dengan environment deployment dan uji dependensi sebelum digunakan.

## Menjalankan aplikasi secara lokal

### 1. Clone repository

```bash
git clone https://github.com/andrifernandiarif/AMIGA-Invoice-to-voucher-AI.git
cd amiga_robot_admin
```

### 2. Konfigurasi backend

```bash
cd backend
python -m venv .venv
```

Aktifkan virtual environment:

**Windows PowerShell**
```powershell
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux**
```bash
source .venv/bin/activate
```

Instal dependensi:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Siapkan variabel environment untuk koneksi database, Redis, AWS, dan konfigurasi aplikasi lain yang diperlukan. Periksa file konfigurasi di `backend/app/` untuk mengetahui nama variabel yang benar; jangan memasukkan kredensial ke source code atau Git.

Jalankan API dari direktori `backend`:

```bash
uvicorn app.main:app --reload
```

Backend berjalan secara default di `http://127.0.0.1:8000`.

- Health check: `http://127.0.0.1:8000/health`
- Dokumentasi API Swagger: `http://127.0.0.1:8000/docs`

> Backend menginisialisasi worker ekstraksi dan indexing saat aplikasi dimulai. Pastikan layanan dan konfigurasi yang diperlukan tersedia. Untuk deployment multi-worker, evaluasi strategi worker agar pekerjaan tidak dijalankan berulang oleh setiap proses API.

### 3. Konfigurasi dan jalankan frontend

Buka terminal baru dari root repository:

```bash
cd frontend
npm install
npm run dev
```

Vite biasanya menjalankan frontend di `http://localhost:5173`.

Untuk memeriksa build production:

```bash
npm run build
npm run preview
```

Untuk menjalankan test frontend:

```bash
npm test
```

Frontend perlu diarahkan ke URL backend sesuai mekanisme konfigurasi API yang digunakan oleh source code. Periksa konfigurasi frontend sebelum menetapkan environment variable; nama variabel tidak diasumsikan di README ini.

## API dan health check

Backend menyediakan endpoint health check:

```http
GET /health
```

Contoh respons:

```json
{
  "status": "ok",
  "workflow": "organization-v4"
}
```

Daftar route lain didaftarkan dari modul backend, termasuk operasi, chat, workspace, refinement, authentication, clients, users, requests, admin, dan voucher. Gunakan Swagger UI (`/docs`) untuk melihat path, parameter, dan skema request terbaru.

## Pengujian

Repository menyediakan direktori `test_invoices/` dan skrip `test_bedrock.py` untuk membantu pengujian. Sebelum menjalankan pengujian yang terhubung ke layanan eksternal:

1. Pastikan konfigurasi environment telah benar.
2. Gunakan dokumen uji yang tidak berisi data sensitif.
3. Pastikan kredensial AWS dan akses layanan tersedia bila diperlukan.
4. Verifikasi hasil ekstraksi dan voucher sebelum menggunakannya untuk proses bisnis.

## Keamanan

- Simpan secret dan kredensial di environment atau secret manager.
- Jangan commit file `.env`, kredensial AWS, atau invoice asli yang berisi informasi rahasia.
- Batasi CORS ke origin frontend yang benar untuk setiap environment. Konfigurasi backend saat ini mengizinkan `http://localhost:5173`; sesuaikan sebelum deployment.
- Gunakan HTTPS dan autentikasi yang sesuai saat aplikasi dipublikasikan.
- Validasi hasil AI dan dokumen invoice sebelum dijadikan dasar transaksi atau pencatatan akuntansi.

## Troubleshooting

| Masalah | Pemeriksaan |
|---|---|
| Frontend tidak bisa mengakses API | Periksa URL backend, CORS, dan apakah API sedang berjalan. |
| API gagal dimulai | Periksa dependensi, environment variable, dan koneksi database/Redis. |
| Proses invoice tidak berjalan | Periksa log backend, worker ekstraksi, akses penyimpanan, dan konfigurasi AI. |
| PDF gagal dibuat | Periksa dependensi sistem yang dibutuhkan WeasyPrint serta log rendering. |
| Integrasi AWS gagal | Periksa AWS credentials, region, IAM permissions, dan akses layanan/model yang digunakan. |

## Kontribusi

1. Buat branch untuk perubahan.
2. Jelaskan tujuan dan dampak perubahan.
3. Jalankan build dan pengujian yang relevan.
4. Buat pull request dengan ringkasan perubahan dan langkah verifikasi.

## Lisensi

Lisensi belum dicantumkan dalam informasi repository yang diperiksa. Pastikan ketentuan lisensi ditetapkan sebelum mendistribusikan atau menggunakan project ini secara komersial.
