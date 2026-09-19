Jable 公开演员资料与普通头像归档

collector.db：仅含 Jable 的演员、观测、采集运行及头像候选。
media/：已核验 SHA-256 的原始普通头像，数据库采用相对路径。
selfdeepsearch-export/：供后续接入的演员与媒体 staging JSONL，不是正式发布清单。
data-audit.json：文件核验结果及每位演员的头像缺项。
archive-manifest.json：文件大小和校验值。

请整体保存或搬迁本目录，不要只复制数据库。使用代码仓库中的 build-performer-showcase 可重新生成离线预览。
资料保持 needs_review / staging。此归档不保证全站完整，不包含成人视频或露骨图片。
