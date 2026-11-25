# Bài báo arXiv

`main.tex` là bản preprint tiếng Anh theo mẫu arXiv. Bản này dùng pdfLaTeX, vì arXiv biên dịch bài từ mã nguồn bằng pdfLaTeX.

```powershell
pdflatex main.tex; pdflatex main.tex
```

## Ảnh

Đặt ảnh vào thư mục `figures/` với đúng các tên dưới đây. Ảnh nào chưa có thì bản PDF hiện một khung trống tại vị trí đó.

| File | Nội dung |
|---|---|
| `neo4j_evidence.png` | Một quan hệ truy ngược về câu bằng chứng và bài báo trong Neo4j |
| `neo4j_path.png` | Đường đi ngắn nhất trong mạng đồng đội |
| `app_portrait.png` | Trang "Chân dung một nút" của ứng dụng |
| `app_qa.png` | Trang "Hỏi đáp đa bước", có phần truy vết và đồ thị con |

## Nộp lên arXiv

Nén `main.tex` và thư mục `figures/` thành một file zip. Tài khoản đăng bài lần đầu cần được endorse trong mục cs.SI hoặc cs.CL.
