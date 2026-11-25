# Hướng dẫn viết bộ câu hỏi v3 (đánh giá thật của chatbot)

Bộ v2/v2b được sinh tự động từ cùng một bộ mẫu câu, nên chỉ đo được khả năng đổi thực thể chứ chưa đo được khả năng hiểu cách hỏi tự nhiên. Bộ v3 do chính hai thành viên viết tay. Đây là con số sẽ đưa vào báo cáo.

## Quy tắc

1. **Viết trước khi xem kết quả của chatbot** và không sửa câu cho dễ hơn sau khi thấy chatbot trả lời sai.
2. **Mỗi người viết khoảng 30 câu**, tổng khoảng 60 câu. Chép từ file mẫu `configs/chatbot_benchmark_v3_template.json` sang `configs/chatbot_benchmark_v3.json`.
3. **Đáp án kiểm tra trên Neo4j Browser** (http://localhost:7474), không ghi theo trí nhớ, vì đáp án đúng là "những gì đồ thị có".
4. Chia đều các loại câu sau:

| `kind` | Số câu | Ví dụ |
|---|---|---|
| `lookup` (1 bước) | 12 | "Haaland đang đá cho ai?" |
| `multihop` (2–3 bước) | 14 | "HLV của các đồng đội cũ của Rooney giờ dẫn dắt đội nào?" |
| `intersection` | 8 | "Cầu thủ nào từng chơi cho cả Chelsea và Inter?" |
| `count` | 8 | "Mourinho có bao nhiêu học trò trong dữ liệu?" |
| `analytics` (phân tích mạng) | 8 | "Ai nằm trong lõi mạng đồng đội?", "Rooney nối với Salah qua những ai?" |
| `out_of_scope` | 5 | Câu ngoài bóng đá, hoặc hỏi tỉ số trận đấu |
| `empty` (không có dữ liệu) | 5 | "Klopp từng bị CLB nào sa thải?" |

5. **Nên có cách hỏi khó**:
   - không dấu;
   - biệt danh ("MU", "Pep", "Quỷ đỏ", "Pháo thủ");
   - chỉ ghi họ ("Arteta");
   - sai chính tả nhẹ;
   - đảo trật tự câu;
   - câu bị động ("bị ai chỉ trích").

## Các trường

- `question`: câu hỏi đúng như người dùng sẽ gõ.
- `expected_entities`: tên chuẩn (canonical_name trên Neo4j) mà bộ liên kết thực thể phải tìm ra. Trường này dùng để chấm riêng bước liên kết.
- `answer`: danh sách tên, hoặc một con số. `null` nếu là câu ngoài phạm vi.
- `answer_type`: `list`, `count` hoặc `unsupported`.
- `note`: điểm khó của câu, dùng khi phân tích lỗi.

## Truy vấn Neo4j giúp tìm đáp án

```cypher
// Mọi quan hệ trực tiếp của một người
MATCH (p:Person {canonical_name: 'Mikel Arteta'})-[r]-(x:FootballEntity)
RETURN type(r), x.canonical_name LIMIT 100;

// Tìm tên chuẩn khi không chắc cách viết
MATCH (n:FootballEntity) WHERE toLower(n.canonical_name) CONTAINS 'arteta'
RETURN n.canonical_name, labels(n);
```
