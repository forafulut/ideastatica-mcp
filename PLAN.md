# IDEA StatiCa MCP Sunucusu – Detaylı Plan

Bu belge, Claude (Claude Desktop / Claude Code) ile IDEA StatiCa Connection arasında
çalışan MCP (Model Context Protocol) sunucusunun tasarımını, kapsamını ve doğrulama
stratejisini tanımlar. Uygulama bu plana göre yapılmıştır; durum bölümü en altta.

## 1. Keşif bulguları (ortam ve kaynaklar)

| Konu | Bulgu |
|---|---|
| Kurulu IDEA StatiCa | `C:\Program Files\IDEA StatiCa\StatiCa 26.0`, servis sürümü **26.0.5.1427** |
| REST servisi | `IdeaStatiCa.ConnectionRestApi.exe -port=<N>`; hazır olunca `GET /heartbeat` 200 döner; Swagger UI kök adreste |
| API sürümü | Kurulu servis ve eşleşen PyPI paketi (`ideastatica-connection-api==26.0.5.1427`) **`/api/4/`** taban yolunu kullanır (dokümanlardaki `/api/3` eski) |
| Resmi Python SDK | 17 API sınıfı (Project, Connection, Member, Operation, Template, ConnectionLibrary, Parameter, LoadEffect, Material, Calculation, CalculationJobs, Report, Export, Conversion, Settings, Presentation, Client) + dosya yardımcıları (ext) |
| Parametre rehberi | NCalc tabanlı ifade dili (fonksiyonlar, birim dönüşümleri, `GetValue`, `Bolts(...)`, `IProfile(...)` vb.); ifadeler daima **string** ve **SI birimlerinde** |
| MCP Python SDK | 2.2.0 – `FastMCP` yerine `MCPServer` (`mcp.server.mcpserver`), senkron araçlar otomatik olarak iş parçacığında çalışır, `Context` parametre enjeksiyonu |
| Çalışma ortamı | Python 3.14 (Node/git yok) → sunucu **Python** ile yazıldı; sanal ortam `.venv` |
| Lisans/servis | Bu makinede servis lisans koltuğu ayırma aşamasında takıldı (lisans sunucusuna ağ erişimi yok); canlı uçtan uca test bu oturumda yapılamadı, mock + protokol testleri yapıldı |

## 2. Hedefler

1. Claude'un IDEA StatiCa Connection'ı **tam iş akışı** boyunca sürebilmesi:
   servisi başlat → proje aç → bağlantı/eleman/operasyon/parametre/yük incele ve değiştir →
   hesapla → sonuçları oku → rapor/dışa aktarım üret → projeyi kaydet.
2. Resmi SDK'yı (`ideastatica-connection-api`) kullanmak; uç nokta uydurmamak.
3. Parametre referans rehberini Claude'a **kaynak (resource)** olarak sunmak, böylece
   doğru ifadeler üretmesi.
4. Uzun hesaplarda ilerleme bildirimi, anlaşılır hata mesajları, büyük sonuçlarda
   kırpma/dosyaya kaydetme.
5. Claude Desktop ve Claude Code için tek satırlık kurulum.

## 3. Mimari

```
Claude Desktop / Claude Code
        │  stdio (JSON-RPC, MCP)
        ▼
ideastatica_mcp (Python, MCPServer)
  ├─ config.py      : ortam değişkenleri, kurulum dizini otomatik tespiti
  ├─ service.py     : IdeaStatiCa.ConnectionRestApi.exe yaşam döngüsü (başlat/durdur/heartbeat,
  │                   Windows Job Object ile yetim süreç engelleme, stdout/stderr log dosyasına)
  ├─ session.py     : SDK istemcisi (ConnectionApiClient) + açık proje kayıtları + yeniden bağlanma
  ├─ serialize.py   : SDK modelleri → JSON, sonuç özetleme, metin kırpma
  ├─ errors.py      : ApiException/ağ hataları → okunabilir ToolError
  ├─ tools/         : araç grupları (aşağıdaki katalog)
  ├─ resources/     : parametre referansı, parametre başlangıç rehberi, API referansı/kavramlar
  ├─ prompts.py     : hazır iş akışı istemleri
  └─ server.py      : MCPServer kurulumu, talimatlar, kayıt
        │  HTTP (urllib3, SDK)
        ▼
IdeaStatiCa.ConnectionRestApi.exe  (localhost:<port>, /api/4)
```

Tasarım kararları:

- **Taşıma:** stdio (varsayılan). İsteğe bağlı `--transport streamable-http`.
- **Durum:** Tek servis süreci + tek SDK istemcisi. Açık projeler sunucu tarafında
  tutulur; MCP oturumu proje kimliklerini ve kaynak dosya yollarını hatırlar.
  `project_id` verilmezse **aktif proje**, `connection_id` verilmezse projede **tek
  bağlantı** varsa o kullanılır.
- **Eşzamanlılık:** Araçlar senkron; MCP SDK bunları iş parçacığında çalıştırır.
  İstemci oluşturma kilitle korunur.
- **Hesaplama:** `calculate-async` işi başlatılır, `calculation-jobs/{id}` yoklanır,
  `ctx.report_progress` ile ilerleme bildirilir; eski servislerde senkron
  `calculate` yedeği vardır.
- **Dosya üretimi:** Raporlar, dışa aktarımlar ve kaydedilen projeler
  `IDEASTATICA_OUTPUT_DIR` (varsayılan `~/Documents/IdeaStatiCa-MCP`) altına yazılır;
  mevcut dosyanın üzerine yazmak için `overwrite=true` gerekir.
- **Güvenlik:** stdout yalnızca MCP protokolü için kullanılır; tüm loglar stderr ve
  `%LOCALAPPDATA%\ideastatica-mcp\logs`. Servis alt süreci stdout'u dosyaya yönlendirilir.

## 4. Yapılandırma (ortam değişkenleri)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `IDEASTATICA_SETUP_DIR` | otomatik (en yeni `StatiCa *`) | `IdeaStatiCa.ConnectionRestApi.exe` dizini |
| `IDEASTATICA_API_URL` | – | Zaten çalışan bir servise bağlan (attach modu) |
| `IDEASTATICA_PORT` | boş port | Servisi başlatırken kullanılacak port |
| `IDEASTATICA_AUTOSTART` | `true` | Gerekince servisi otomatik başlat |
| `IDEASTATICA_START_TIMEOUT` | `240` sn | Heartbeat bekleme süresi (lisans kontrolü uzun sürebilir) |
| `IDEASTATICA_OUTPUT_DIR` | `~/Documents/IdeaStatiCa-MCP` | Üretilen dosyalar |
| `IDEASTATICA_LOG_LEVEL` | `INFO` | Log seviyesi |
| `IDEASTATICA_MAX_TEXT_CHARS` | `60000` | Araç yanıtlarında metin kırpma sınırı |

## 5. Araç kataloğu (MCP tools)

**Servis:** `service_status`, `service_start`, `service_stop`

**Proje:** `open_project`, `create_project`, `list_open_projects`, `get_project`,
`update_project_info`, `save_project`, `close_project`, `update_project_from_iom`

**Bağlantı:** `list_connections`, `get_connection`, `update_connection`,
`create_connection`, `copy_connection`, `delete_connection`, `get_connection_topology`,
`get_production_cost`

**Eleman (member):** `list_members`, `get_member`, `add_member`, `update_member`,
`set_bearing_member`, `delete_member`

**Operasyon:** `list_operations`, `delete_all_operations`,
`get_common_operation_properties`, `update_common_operation_properties`, `predesign_welds`

**Parametre:** `list_parameters`, `update_parameters`, `evaluate_expression`,
`delete_all_parameters`

**Yük etkileri:** `list_load_effects`, `get_load_effect`, `add_load_effect`,
`update_load_effect`, `delete_load_effect`, `get_load_settings`, `set_load_settings`,
`calculate_load_extremes`

**Malzeme:** `list_project_materials`, `add_project_material`, `add_cross_section`,
`search_material_library`

**Hesap/sonuç:** `calculate`, `get_calculation_job`, `cancel_calculation_job`,
`get_check_results`, `get_raw_results`

**Şablon & kütüphane:** `export_connection_template`, `get_template_mapping`,
`apply_template`, `list_applied_templates`, `delete_template`, `explode_template`,
`load_template_defaults`, `get_template_common_properties`,
`update_template_common_properties`, `list_library_design_sets`,
`propose_library_designs`, `get_library_template`, `apply_library_design`,
`save_library_design_picture`, `publish_connection_to_library`

**Tasarım kodu dönüşümü:** `get_design_code_conversion_mapping`, `convert_project_design_code`

**Rapor / dışa aktarım / 3B:** `generate_report`, `export_connection`, `get_scene_3d`

**Ayarlar:** `get_project_settings`, `update_project_settings`

**Kaynaklar (resources):** `ideastatica://docs/parameter-reference`,
`ideastatica://docs/parameters-getting-started`, `ideastatica://docs/api-reference`,
`ideastatica://docs/api-concepts`, `ideastatica://status`, `ideastatica://projects`

**İstemler (prompts):** `calculate_and_report`, `parametric_study`, `design_from_library`

## 6. Uygulama aşamaları

1. Keşif: repo, dokümanlar, kurulu servis, SDK yüzeyi (introspection) – tamamlandı.
2. Temel altyapı: config, service, session, serialize, errors.
3. Araç grupları (yukarıdaki katalog) + sunucu talimatları.
4. Kaynaklar ve istemler (parametre rehberi gömülü).
5. Testler: birim (serileştirme, yapılandırma), sahte istemci ile araç testleri,
   stdio üzerinden gerçek MCP el sıkışma testi, SDK sözleşme testi (kullanılan her SDK
   metodu kurulu pakette var mı), canlı test (`IDEASTATICA_LIVE=1` ile).
6. Kurulum: `.mcp.json` (Claude Code), Claude Desktop yapılandırma girdisi
   (yedek alınarak), `scripts/setup.ps1`, README.

## 7. Riskler ve önlemler

- **Sürüm uyumsuzluğu:** SDK sürümü kurulu servisle eşleşmeli; `service_status`
  servis ve SDK sürümlerini karşılaştırıp uyarır.
- **Lisans:** Servis, lisans koltuğu ayırır; Basic lisansta çalışmaz. Başlatma zaman
  aşımında servis logundan ipucu döndürülür.
- **Büyük yanıtlar:** Ham sonuçlar ve 3B sahne verisi dosyaya yazılır, yanıt kırpılır.
- **İstemci zaman aşımı:** MCP istemcileri (Claude Code ~60 sn) uzun araç çağrılarını keser ve
  sunucuyu yeniden başlatır. Bu yüzden başlatma beklemesi çağrı başına sınırlıdır
  (`START_WAIT`), servis arka planda başlamaya devam eder ve durum dosyası sayesinde
  yeniden başlayan MCP sunucusu aynı servise bağlanır.
- **Yetim süreç:** Servis `service_stop` ile durdurulur; `KILL_ON_EXIT=true` ile Job Object
  bağlaması açılabilir.

## 7b. Genişletmeler (07.10.2026)

- Operasyon düzenleme (kaynak, bulon ızgarası, kesim, plaka …) için servisteki gizli
  `operations/{kind}` uç noktaları ham REST ile sarıldı (`tools/operations.py`).
- İş akışı araçları: parametre taraması, tasarım özeti, otomatik kaynak boyutlandırma, toplu
  hesap, kütüphane puanlama (`tools/workflows.py`).
- Görsel: 3B sahne → PNG (Pillow), rapor sayfası → PNG (PyMuPDF) (`tools/visual.py`).
- IOM inceleme/basit üretim (`tools/iom.py`), RCS servisi ham REST (`tools/rcs.py`).
- Performans: ön ısıtma, heartbeat'siz çağrı, bağlantı önbelleği, hızlı yoklama, kompakt çıktılar;
  oturum açılışında servis için Task Scheduler betiği.

## 7c. İkinci genişletme (08.10.2026)

- Tasarım danışmanı (`design_advisor`), çok parametreli optimizasyon (`optimize_design`),
  yük kombinasyonu üretimi, parametre tanılama (`tools/advisor.py`).
- Şablon XML düzenleme + kişisel şablon kütüphanesi (`tools/template_edit.py`) – kapalı
  operasyon API'sinin yerine geçer.
- Oran haritası, sonuç tabloları, HTML özet raporu (`tools/visual.py`).
- Anlık görüntü/geri alma, dry-run, sürümlü kayıt (`session.snapshot`, `projects.py`,
  `parameters.py`); `ideastatica://jobs/{id}` kaynağı; çok katlı çerçeve IOM üretici.
- Oturum açılışında servis için Task Scheduler görevi kaydedildi (port 5055,
  `IDEASTATICA_API_URL` kullanıcı değişkeni).
- Bulgu: `operations/{kind}` ve `result-mesh` rotaları 26.0.5 servisinde 404 (gizli);
  lisans "Sign in" penceresi çözücüyü de bloke eder (hesap işi bitmez).

## 8. Durum

Bkz. README.md "Durum / Doğrulama" bölümü ve test çıktıları.
