# ideastatica-mcp

Claude (Claude Desktop / Claude Code) ile **IDEA StatiCa Connection** arasında çalışan bir
MCP (Model Context Protocol) sunucusu. IDEA StatiCa ile birlikte kurulan yerel REST servisi
(`IdeaStatiCa.ConnectionRestApi.exe`, API 4.0) üzerinden projeyi açar, bağlantıları, elemanları,
parametreleri ve yükleri okur/değiştirir, CBFEM hesabı yapar, sonuçları özetler, rapor ve
dışa aktarım üretir, projeyi kaydeder.

Resmi Python SDK'sı (`ideastatica-connection-api`, kurulu IDEA StatiCa sürümüyle eşleşen
26.0.x) kullanılır; hiçbir uç nokta elle uydurulmamıştır. Tasarım ve kapsam için
[PLAN.md](PLAN.md).

## Gereksinimler

- Windows, IDEA StatiCa **24.1 veya üstü** (bu kurulum: 26.0, `C:\Program Files\IDEA StatiCa\StatiCa 26.0`)
- Geçerli IDEA StatiCa lisansı (Basic lisans API'yi desteklemez; servis lisans koltuğu ayırır)
- Python 3.11+ (bu makinede 3.14)
- `git` (depoyu klonlamak için) veya GitHub'dan ZIP indirme

## Depoda neler var, neler yok

Bu depo yalnızca kaynak kodu içerir (yaklaşık 2 MB). Aşağıdakiler **depoda yoktur**, her
kullanıcı kendi makinesine indirir/kurar:

| Ne | Boyut | Nereden gelir |
|---|---|---|
| Python bağımlılıkları (`mcp`, `ideastatica-connection-api`, `pymupdf`, `pillow`, `pydantic`, `anyio`) ve sanal ortam `.venv` | ≈ 170 MB | `pip` ile PyPI'dan; `scripts\setup.ps1` otomatik yapar |
| **IDEA StatiCa** programı ve lisansı (servis `IdeaStatiCa.ConnectionRestApi.exe`) | – | [IDEA StatiCa](https://www.ideastatica.com) kurulumu; bu depoda dağıtılmaz |

Bu yüzden `.venv` klasörünü GitHub'a **yüklemeyin** (`.gitignore` zaten dışlar). Yüklenen paketin
büyük görünmesinin tek nedeni genellikle `.venv`'dir.

## İndirme

```powershell
git clone https://github.com/<kullanici>/ideastatica-mcp.git
cd ideastatica-mcp
```

`git` yoksa depo sayfasında **Code → Download ZIP** ile indirip klasöre çıkarın ve o klasörde
PowerShell açın.

## Kurulum

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

`setup.ps1` sanal ortamı (`.venv`) oluşturur, paketi ve bağımlılıkları PyPI'dan indirip kurar
(`mcp`, `ideastatica-connection-api==26.0.*` …) ve SDK sözleşme testini çalıştırır.

Betik kullanmadan elle kurulum:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"      # veya: pip install -r requirements.txt
```

**Önemli – SDK sürümü kurulu IDEA StatiCa ile eşleşmeli.** Bağımlılık
`ideastatica-connection-api>=26.0.5,<26.1` olarak sabitlenmiştir. Farklı bir IDEA StatiCa
sürümü kullanıyorsanız eşleşen SDK'yı kurun (sürüm numarası IDEA StatiCa'nınkiyle aynı olmalı):

```powershell
.\.venv\Scripts\python.exe -m pip install "ideastatica-connection-api==<IDEA sürümü>.*"
```

(Eşleşme `service_status` çıktısındaki `sdk_matches_service` alanından doğrulanır.)

### Claude Desktop

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_claude_desktop.ps1
```

Betik `%APPDATA%\Claude\claude_desktop_config.json` dosyasına (yedek alarak) şu girdiyi ekler:

```json
{
  "mcpServers": {
    "ideastatica": {
      "command": "C:\\KLASOR\\ideastatica-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "ideastatica_mcp"],
      "env": { "IDEASTATICA_LOG_LEVEL": "INFO" }
    }
  }
}
```

(`C:\KLASOR\ideastatica-mcp` yerine depoyu klonladığınız klasörün tam yolunu yazın; betik bunu
sizin yerinize doldurur.) Elle yazmak isterseniz örnek:
[claude_desktop_config.example.json](claude_desktop_config.example.json).

Claude Desktop'ı yeniden başlatın. Sohbette "IDEA StatiCa servis durumunu göster" demek
`service_status` aracını çağırır.

### Claude Code

Proje klasöründeki [.mcp.json](.mcp.json) sunucuyu otomatik tanımlar. Başka bir klasörden
kullanmak için:

```bash
claude mcp add ideastatica -- "C:\KLASOR\ideastatica-mcp\.venv\Scripts\python.exe" -m ideastatica_mcp
```

`C:\KLASOR\ideastatica-mcp` yerine kendi klasör yolunuzu yazın. `.mcp.json` içindeki yol da
kendi klasörünüze göre düzenlenmelidir.

## Nasıl çalışır

```
Claude ──stdio/MCP──▶ ideastatica_mcp ──HTTP──▶ IdeaStatiCa.ConnectionRestApi.exe (localhost)
```

- İlk araç çağrısında servis otomatik başlatılır (boş bir portta) ve `/heartbeat` beklenir.
  Lisans kontrolü uzun sürebildiğinden bir araç çağrısı en fazla `IDEASTATICA_START_WAIT`
  (40 sn) bekler; süre dolarsa "still starting" der, servis arka planda başlamaya devam eder,
  bir sonraki çağrı (ör. `service_status`) hazır olunca bağlanır.
- Çalışan servis `%LOCALAPPDATA%\ideastatica-mcp\logs\service-state.json` ile hatırlanır:
  MCP sunucusu yeniden başlarsa aynı servise yeniden bağlanır (adopt), lisans başlatması
  tekrar ödenmez. Servis yalnızca `service_stop` ile (veya `IDEASTATICA_KILL_ON_EXIT=true`
  ise MCP kapanınca) durur.
- Projeler servis belleğinde açık kalır; `save_project` çağrılana kadar diske yazılmaz.
- `project_id` verilmezse son açılan proje, `connection_id` verilmezse projedeki tek bağlantı
  kullanılır.
- Üretilen dosyalar (rapor, IFC/IOM/DWG, kaydedilen proje, ham sonuçlar)
  `%USERPROFILE%\Documents\IdeaStatiCa-MCP` altına yazılır; mevcut dosya `overwrite=true`
  olmadan ezilmez.
- Loglar: stderr ve `%LOCALAPPDATA%\ideastatica-mcp\logs\` (servis çıktısı da orada).

## Araçlar (118)

**Yeni gruplar (07.10.2026)**

| Grup | Araçlar |
|---|---|
| Operasyon düzenleme (önizleme) | `list_operation_details`, `get_operation_details`, `update_operation`, `add_operation`, `delete_operation` – kaynak (weld), bulon ızgarası, kesim, plaka vb. operasyonların tam tanımını okur/değiştirir. Servisin DLL'inde bulunan `operations/weld`, `bolt-grid`, `plate-cut`, `stiffening-plate`, `work-plane` … rotalarını kullanır. **Canlı test (07.10.2026, servis 26.0.5.1427): bu rotalar henüz etkin değil, 404 dönüyor**; araçlar bunu açıklayan bir mesaj verir. IDEA StatiCa operasyon API'sini yayınladığında kod değişikliği gerekmeden çalışacaktır. Kaynak boyutlandırma için `auto_weld_sizing` kullanılır. |
| İş akışları | `optimize_parameter` (parametre taraması + tablo + en ekonomik geçen değer), `design_check_summary`, `auto_weld_sizing`, `batch_calculate` (klasör), `score_library_designs` |
| Görsel | `render_connection_image` (3B model → PNG, Claude'a görüntü döner), `render_report_pages` (PDF rapor sayfaları → PNG) |
| IOM | `inspect_iom_file`, `build_iom_frame` (basit kolon-kiriş IOM şablonu; canlı içe aktarma doğrulandı – yükler `add_load_effect` ile eklenir) |
| RCS (betonarme kesit) | `rcs_service_status`, `rcs_open_project`, `rcs_get_project`, `rcs_get_section_loading`, `rcs_set_section_loading`, `rcs_update_section`, `rcs_calculate`, `rcs_get_results`, `rcs_list_materials`, `rcs_get_code_settings`, `rcs_save_project`, `rcs_close_project`, `rcs_service_stop` – `IdeaStatiCa.RcsRestApi.exe` üzerinden ham REST (API 1) |

`get_raw_results` artık `path="plates[0].items"` / `"welds.*.name"` gibi yol filtresi alır.

**İkinci genişletme (08.10.2026)**

| Grup | Araçlar |
|---|---|
| Tasarım danışmanı | `design_advisor` (yönetici kontrole göre somut öneri; `apply=true` ile uygula + yeniden hesap), `optimize_design` (çok parametreli ızgara arama; maliyet / oran hedefi), `generate_load_combinations` (EC 6.10 veya AISC kombinasyonlarından yük durumları), `diagnose_parameters` (gizli parametreler, sınır ihlalleri, ifade hataları) |
| Şablon düzenleme & kütüphane | `find_template_values`, `edit_template_xml` (operasyon API'si kapalıyken kaynak/bulon/plaka değerlerini şablon XML'inde değiştirip `apply_template` ile uygulama), `save_template_to_library`, `list_saved_templates`, `apply_saved_template`, `delete_saved_template` |
| Görsel | `render_utilization_map` (parçalar kontrol oranına göre yeşil→kırmızı), `get_result_tables` (plaka/kaynak/bulon tabloları), `compile_summary_report` (bulgular + tablolar + görüntüler → tek HTML) |
| Güvenlik | Değişiklik yapan araçlar önce anlık görüntü alır: `list_snapshots`, `undo_last_change`; `update_parameters(dry_run=true)`; `save_project(versioned=true)` (`_v1, _v2 …`) |
| IOM | `build_iom_portal_frame` (çok açıklıklı/katlı çerçeve, her düğüm bağlantı noktası) |
| Kaynak | `ideastatica://jobs/{job_id}` (hesap işi durumu) |

Not: servisin `result-mesh` (gerilme ağı) uç noktası da bu derlemede kapalı; `render_utilization_map` kontrol sonuçlarını (kaynak oranı, plaka plastik şekil değiştirme, bulon oranı) parça bazında renklendirir.

**Temel gruplar**

| Grup | Araçlar |
|---|---|
| Servis | `service_status`, `service_start`, `service_stop` |
| Proje | `open_project` (.ideaCon veya IOM .xml), `create_project`, `list_open_projects`, `get_project`, `update_project_info`, `save_project`, `close_project`, `update_project_from_iom` |
| Bağlantı | `list_connections`, `get_connection`, `update_connection`, `create_connection`, `copy_connection`, `delete_connection`, `get_connection_topology`, `get_production_cost` |
| Eleman | `list_members`, `get_member`, `add_member`, `update_member`, `set_bearing_member`, `delete_member` |
| Operasyon | `list_operations`, `delete_all_operations`, `get_common_operation_properties`, `update_common_operation_properties`, `predesign_welds` |
| Parametre | `list_parameters`, `update_parameters`, `evaluate_expression`, `delete_all_parameters` |
| Yük | `list_load_effects`, `get_load_effect`, `add_load_effect`, `update_load_effect`, `delete_load_effect`, `get_load_settings`, `set_load_settings`, `calculate_load_extremes` |
| Malzeme | `list_project_materials`, `add_project_material`, `add_cross_section`, `search_material_library` |
| Hesap | `calculate` (ilerleme bildirimli iş), `get_calculation_job`, `cancel_calculation_job`, `get_check_results`, `get_raw_results` |
| Şablon / Kütüphane | `export_connection_template`, `get_template_mapping`, `apply_template`, `list_applied_templates`, `delete_template`, `explode_template`, `load_template_defaults`, `get_template_common_properties`, `update_template_common_properties`, `list_library_design_sets`, `propose_library_designs`, `get_library_template`, `apply_library_design`, `save_library_design_picture`, `publish_connection_to_library` |
| Kod dönüşümü | `get_design_code_conversion_mapping`, `convert_project_design_code` |
| Rapor / Dışa aktarım | `generate_report` (pdf/word/html), `export_connection` (ifc/iom/dwg/iom_connection_data), `get_scene_3d` |
| Ayarlar | `get_project_settings`, `update_project_settings` |

Kaynaklar (resources): `ideastatica://docs/parameter-reference` (ifade parametre referansı),
`ideastatica://docs/parameters-getting-started`, `ideastatica://docs/api-reference`,
`ideastatica://docs/api-concepts`, `ideastatica://status`, `ideastatica://projects`.

İstemler (prompts): `calculate_and_report`, `parametric_study`, `design_from_library`.

## Örnek istekler (Claude'a)

- "`C:\Projeler\kiris-kolon.ideaCon` dosyasını aç, bağlantıları ve elemanları listele."
- "Parametreleri göster; `plateThickness` değerini 0.02 yap ve yeniden hesapla, sonuçları karşılaştır."
- "Tüm bağlantıları hesapla, %100'ü aşan kontrolleri vurgula ve PDF rapor üret."
- "Kütüphaneden bu bağlantıya uygun tasarım öner, en uygununu uygula ve kontrol et."
- "Projeyi AISC'ye dönüştür (S 355 → A992) ve yeni dosya olarak kaydet."

## Birimler ve kurallar

- Parametre ifadeleri **string** ve **SI** birimlerinde (m, N, Pa, rad): 15 mm → `"0.015"`;
  metinler tek tırnak içinde (`'M20 8.8'`); diğer parametreye referans `[anahtar]`.
  Fonksiyonlar için parametre referansı kaynağına bakın (`RoundWithOffset`, `GetValue`,
  `Bolts(...)`, `IProfile(...)` vb.).
- Yük etkileri de SI: kuvvetler N, momentler Nm (120 kN → 120000). Kontrol değerleri zaten
  yüzde (85.0 = %85); `skipped=true` olanlar atlanır.
- Plaka/bulon/kaynak tek tek API ile eklenemez: şablon uygula, kütüphaneden tasarım uygula
  veya IOM içe aktar; boyutları parametrelerle sür.

## Yapılandırma (ortam değişkenleri)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `IDEASTATICA_SETUP_DIR` | otomatik (en yeni `StatiCa *`) | Servis exe'sinin klasörü |
| `IDEASTATICA_API_URL` | – | Zaten çalışan servise bağlan (`http://localhost:5000`) |
| `IDEASTATICA_PORT` | boş port | Servisi başlatırken port |
| `IDEASTATICA_AUTOSTART` | `true` | Gerekince servisi otomatik başlat |
| `IDEASTATICA_START_TIMEOUT` | `240` | Toplam başlatma sınırı (sn); aşılırsa servis durdurulur |
| `IDEASTATICA_START_WAIT` | `40` | Tek bir araç çağrısının başlatma için bekleyeceği süre (sn) |
| `IDEASTATICA_KILL_ON_EXIT` | `false` | `true` ise servis MCP sunucusuyla birlikte kapanır (Job Object) |
| `IDEASTATICA_PREWARM` | `true` | MCP açılınca servisi arka planda başlat ve bağlan |
| `IDEASTATICA_DISMISS_SIGNIN` | `true` | Lisansın "Sign in - IDEA StatiCa" penceresini (License.exe) çıktığı anda iptal eder; MCP sunucusu açıldığında ve her bağlantıda başlar. Kapatılan pencereler `signin-guard.log` dosyasına yazılır, durum `service_status` → `signin_guard` |
| `IDEASTATICA_OUTPUT_DIR` | `~/Documents/IdeaStatiCa-MCP` | Üretilen dosyalar |
| `IDEASTATICA_LOG_LEVEL` | `INFO` | Log seviyesi |
| `IDEASTATICA_MAX_TEXT_CHARS` | `60000` | Uzun metin yanıtlarında kırpma sınırı |

Komut satırı: `python -m ideastatica_mcp --help` (`--transport streamable-http --port 8765`
ile HTTP taşıması da mümkündür).

## Lisans

[MIT](LICENSE). IDEA StatiCa programı ve lisansı bu lisansın kapsamında değildir; ayrıca
edinilmelidir.

## Testler

```powershell
.\.venv\Scripts\python.exe -m pytest -q            # sahte SDK istemcisi + stdio el sıkışma
set IDEASTATICA_LIVE=1
.\.venv\Scripts\python.exe -m pytest -q tests\test_live.py -m live -s   # gerçek servis ile
```

- `test_tools.py`: 73 aracın davranışı, gerçek MCP istemcisiyle süreç içinde (fake SDK).
- `test_sdk_contract.py`: araçların çağırdığı her SDK metodu kurulu pakette var mı.
- `test_stdio.py`: sunucu Claude Desktop'ın başlattığı gibi alt süreçte başlatılır, el sıkışma + araç çağrısı.
- `test_live.py`: servisi başlatır, örnek projeyi açar, hesaplar, rapor üretir (lisans gerekir).

## Durum / Doğrulama

- Birim, araç ve stdio testleri geçiyor (38 test).
- Canlı uçtan uca akış bu makinede doğrulandı (30.09.2026): servis ~130 sn'de hazır oldu,
  `simple-project.ideaCon` açıldı, elemanlar/operasyonlar/yükler okundu, CBFEM hesabı 4 sn
  sürdü (kaynaklar %79.5), PDF rapor ve kaydedilmiş proje üretildi.
- Servis başlatması lisans doğrulaması yüzünden 2-3 dakika sürebilir; araçlar "still starting"
  derse bir dakika sonra tekrar deneyin.

## Performans

Ölçüm (07.10.2026, gerçek servis, `simple-project.ideaCon`):

| İşlem | Süre |
|---|---|
| Okuma araçları (list_members, list_parameters, settings …) | 2–20 ms |
| open_project | ~0.5 s |
| calculate (1 bağlantı, iş + yoklama) | ~1.8 s (çözücü süresi) |
| get_raw_results | ~1.5 s (servis JSON üretimi) |
| generate_report (PDF) | ~11 s (servis tarafı) |
| MCP sunucusu açılışı (import) | ~2.5 s, oturum başına bir kez |

Hız için yapılanlar: servis MCP açılır açılmaz arka planda başlatılır ve istemci kaydı
yapılır (`IDEASTATICA_PREWARM`), çağrı başına heartbeat yok (hata olursa yeniden bağlanır),
bağlantı listesi 60 sn önbelleklenir (değişikliklerde temizlenir), hesap işi 0,25 sn'den
başlayan aralıkla yoklanır, büyük çıktılar kompakt döner (`verbose=true` ile tam hali).
Kalan süreler IDEA StatiCa çözücüsü/rapor motorundan gelir ve MCP tarafında kısaltılamaz.

## Sorun giderme

- **"Sign in - IDEA StatiCa" penceresi:** Servis ilk açılışta lisans girişi ister ve bu pencere
  yanıtlanana kadar bekler (giriş yapın ya da iptal edin; iptalde yerel tasarım setleriyle devam
  eder). Kalıcı çözüm: IDEA StatiCa uygulamasında bir kez oturum açmak. Birden fazla MCP
  örneği (Claude Desktop + Claude Code) aynı servisi durum dosyası üzerinden paylaşır; yine de
  ilk açılışta pencere bir kez çıkabilir.
- **Servisi oturum açılışında hazır tutmak:** `scripts\register_autostart.ps1` görevi
  kaydeder (port 5055) ve `IDEASTATICA_API_URL` kullanıcı değişkenini ayarlar; MCP anında bağlanır.
- **Servis başlamıyor / zaman aşımı:** IDEA StatiCa'nın kendisi açılıp giriş yapılabiliyor mu?
  `%LOCALAPPDATA%\ideastatica-mcp\logs\service-<port>.log` ve
  `%TEMP%\IdeaStatica\Logs\ConnectionRestApi.log` dosyalarına bakın. `IDEASTATICA_START_TIMEOUT`
  ile bekleme süresini artırın veya servisi elle başlatıp `IDEASTATICA_API_URL` verin.
- **HTTP 404 / proje bulunamadı:** Servis yeniden başlamış olabilir; projeyi yeniden açın.
- **SDK/servis sürüm uyuşmazlığı:** `service_status` içindeki `sdk_matches_service` alanına
  bakın; `pip install "ideastatica-connection-api==<kurulu sürüm>.*"` ile eşleştirin.
