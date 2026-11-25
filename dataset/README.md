# Premier League Graph: bộ dữ liệu

Mạng xã hội cầu thủ, huấn luyện viên, người đại diện và câu lạc bộ xoay quanh Ngoại hạng Anh. Dữ liệu được trích từ 6.158 bài báo tiếng Việt và tiếng Anh (12/2011–10/2026) bằng Qwen3-4B đã fine-tune, sau đó liên kết và lọc bằng Wikidata. Bộ dữ liệu được sinh lại bằng lệnh `python scripts/export_dataset.py`.

## Cấu trúc

| File | Số dòng | Nội dung |
|---|---|---|
| `graph/nodes.csv` | 2.046 | `entity_id` (mã Wikidata khi có), tên chuẩn, loại `Person`/`Club`, vai trò `Player`/`Coach`/`Agent`, tên khác |
| `graph/edges.csv` | 4.924 | Cạnh trích từ báo thuộc 12 loại quan hệ, kèm số câu bằng chứng, ngôn ngữ, ngày xuất hiện đầu và cuối |
| `graph/edge_sources.csv` | 37.117 | Mỗi cạnh nối với các bài báo làm bằng chứng (link bài) |
| `graph/articles.csv` | 6.158 | Danh mục bài báo: link, tiêu đề, nguồn, ngôn ngữ, ngày đăng (5.278 tiếng Việt, 880 tiếng Anh) |
| `graph/derived/teammate_of.csv` | 41.262 | Hai cầu thủ cùng CLB trong giai đoạn trùng nhau ít nhất 3 tháng (từ mốc thời gian sự nghiệp trên Wikidata) |
| `graph/derived/played_under.csv` | 5.427 | Cầu thủ thi đấu dưới quyền một HLV (cùng CLB, cùng giai đoạn) |
| `graph/derived/transfer_flow.csv` | 2.674 | Dòng cầu thủ giữa hai CLB liên tiếp trong sự nghiệp, kèm số lượt |
| `comparison_14b/` | 2.147 nút / 5.111 cạnh | Graph do Qwen3-14B trích xuất, dùng làm đối chứng |
| `analysis/` | | Kết quả phân tích mạng (thế giới nhỏ, cộng đồng, nhân vật trung tâm, k-core) và thống kê graph |
| `evaluation/` | 150 + 60 | Mẫu cạnh đã chấm đúng/sai; mẫu 60 cạnh của graph 4B mới được chấm sơ bộ |

Các bộ câu hỏi đánh giá chatbot nằm trong `configs/chatbot_benchmark*.json` ở gốc repository.

## 12 loại quan hệ

| Quan hệ | Chiều | Quan hệ | Chiều |
|---|---|---|---|
| `PLAYS_FOR` | người → CLB | `APPOINTED_MANAGER_OF` | HLV → CLB |
| `TRANSFERRED_TO` | người → CLB (đội nhận) | `DISMISSED_MANAGER` | CLB → HLV |
| `LOANED_TO` | người → CLB | `RESIGNED_FROM` | HLV → CLB |
| `MANAGES` | HLV → CLB | `REPRESENTS` | người đại diện → người |
| `EXTENDED_CONTRACT_WITH` | người → CLB | `CRITICIZES` | người → người/CLB |
| `NEGOTIATES_TO_SIGN` | CLB → người | `CONFLICTS_WITH` | người → người/CLB |

## Những gì không công bố

Toàn văn bài báo và câu bằng chứng nguyên văn thuộc bản quyền của các toà soạn (VnExpress, Nhân Dân, Việt Nam News, Bóng Đá+), nên không đưa vào bộ dữ liệu. Mỗi cạnh vẫn truy được về bài gốc qua `edge_sources.csv`, và có thể tải lại nội dung bài từ các link đó.
