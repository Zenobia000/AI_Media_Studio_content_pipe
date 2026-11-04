-- Gives the parity dataset the article shapes the item page reads: full bodies with headings, pictures and a video, Chinese and translated
-- bodies (complete and partial), a post's translation, a summary-only report and a withdrawn one. Run after seed-parity.ts.
WITH picked AS (SELECT article_id, row_number() OVER (ORDER BY article_id) n FROM publications WHERE channel = 'news')
UPDATE articles a SET
  body_html = CASE n % 4
    WHEN 0 THEN '<h2>Overview</h2><p>First paragraph &amp; more.</p><h3>Details</h3><p><img src="https://example.org/pic' || n || '.png" width="900" height="500" alt="pic"></p><p>End.</p>'
    WHEN 1 THEN '<p>Short body with <a href="https://example.org/x">a link</a>.</p><video src="https://example.org/v' || n || '.mp4" poster="https://example.org/poster' || n || '.jpg"></video>'
    WHEN 2 THEN '<h2>中文標題</h2><p>這是一篇中文文章的內文。</p><p><img src="https://example.org/zh' || n || '.png" alt="圖"></p>'
    ELSE '<h4>Deep</h4><p>Plain text only.</p><p><img src="https://example.org/s' || n || '.png" width="1" height="1"></p>' END,
  body_text = CASE n % 4 WHEN 2 THEN '這是一篇中文文章的內文。' ELSE 'Body text of article ' || n || '.' END,
  language = CASE n % 4 WHEN 2 THEN 'zh' ELSE 'en' END
FROM picked WHERE a.id = picked.article_id AND n <= 16;
WITH picked AS (SELECT article_id, row_number() OVER (ORDER BY article_id) n FROM publications WHERE channel = 'news')
UPDATE publications p SET body_mode = 'full' FROM picked WHERE p.article_id = picked.article_id AND n <= 16;
WITH picked AS (SELECT a.id, row_number() OVER (ORDER BY a.id) n, a.revision FROM articles a JOIN publications p ON p.article_id = a.id WHERE p.channel = 'news')
INSERT INTO translations (article_id, lang, revision, title, body_html, body_text, complete)
SELECT id, 'zh', revision, '譯文標題 ' || n, '<h2>譯文</h2><p>這是第 ' || n || ' 篇的譯文。</p>', '這是第 ' || n || ' 篇的譯文。', n % 8 <> 1
FROM picked WHERE n <= 16 AND n % 4 <> 2 ON CONFLICT DO NOTHING;
INSERT INTO translations (article_id, lang, revision, body_text, complete)
SELECT a.id, 'zh', a.revision, '這是一則貼文的中文翻譯。', true FROM articles a JOIN publications p ON p.article_id = a.id WHERE p.channel = 'x' ON CONFLICT DO NOTHING;
UPDATE publications SET visibility = 'summary-only' WHERE article_id = (SELECT article_id FROM publications WHERE channel = 'news' AND body_mode = 'summary' ORDER BY article_id LIMIT 1 OFFSET 3);
UPDATE publications SET visibility = 'withdrawn' WHERE article_id = (SELECT article_id FROM publications WHERE channel = 'news' AND body_mode = 'summary' ORDER BY article_id LIMIT 1 OFFSET 5);
UPDATE publications SET syndicate = true WHERE body_mode = 'full' OR channel = 'x' OR article_id IN (SELECT article_id FROM publications WHERE selected ORDER BY article_id LIMIT 12);
