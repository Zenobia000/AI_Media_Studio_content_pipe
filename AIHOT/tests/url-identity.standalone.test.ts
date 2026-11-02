import assert from "node:assert/strict";
import { test } from "node:test";
import { identityKeyForUrl, normalizeUrl, tweetIdFromUrl } from "../packages/backend/src/lib/url.ts";

test("X and Twitter status URL variants keep the same material identity", () => {
  for (const host of ["x.com", "twitter.com", "www.x.com", "www.twitter.com", "mobile.twitter.com", "m.twitter.com"]) {
    for (const path of ["/openai/status/1234567890", "/openai/statuses/1234567890", "/i/status/1234567890"]) {
      const url = `https://${host}${path}?s=20#thread`;
      assert.equal(tweetIdFromUrl(url), "1234567890", url);
      assert.equal(identityKeyForUrl(url), "x:1234567890", url);
    }
  }
  assert.equal(tweetIdFromUrl("http://TWITTER.COM/openai/status/1234567890/photo/1"), "1234567890");
});

test("other sites containing a tweet URL keep their own material identity", () => {
  const tweet = "https://x.com/openai/status/1234567890";
  const urls = [
    `https://news.example.org/article/42?related=${tweet}`,
    `https://news.example.org/article/42#${tweet}`,
    `https://news.example.org/archive/${tweet}`,
    "https://notx.com/openai/status/1234567890",
    "https://nottwitter.com/openai/status/1234567890",
    "https://x.com.example.org/openai/status/1234567890",
    "https://x.com@news.example.org/openai/status/1234567890",
  ];
  for (const url of urls) {
    assert.equal(tweetIdFromUrl(url), null, url);
    assert.equal(identityKeyForUrl(url), `url:${normalizeUrl(url)}`, url);
    assert.notEqual(identityKeyForUrl(url), identityKeyForUrl(tweet), url);
  }
});

test("only a complete status id at the expected path identifies a tweet", () => {
  for (const url of [
    "https://x.com/search?q=https://x.com/openai/status/1234567890",
    "https://x.com/archive/openai/status/1234567890",
    "https://x.com/openai/status/1234567890junk",
    "https://x.com/openai/status/",
    "https://x.com/openai/status/not-a-number",
  ]) assert.equal(tweetIdFromUrl(url), null, url);
});

test("invalid and non-HTTP URLs cannot acquire a tweet identity", () => {
  for (const url of ["not a URL", "x.com/openai/status/1234567890", "ftp://x.com/openai/status/1234567890"]) {
    assert.equal(tweetIdFromUrl(url), null, url);
    assert.equal(identityKeyForUrl(url), null, url);
  }
});

test("arXiv, YouTube and GitHub repository URLs keep one identity across their forms", () => {
  const same: Record<string, string[]> = {
    "arxiv:2410.12345": ["https://arxiv.org/abs/2410.12345", "https://arxiv.org/abs/2410.12345v3", "http://www.arxiv.org/pdf/2410.12345v2.pdf", "https://arxiv.org/html/2410.12345v1/", "https://arxiv.org/abs/2410.12345?utm_source=x"],
    "arxiv:hep-th/9901001": ["https://arxiv.org/abs/hep-th/9901001", "https://arxiv.org/pdf/hep-th/9901001v2"],
    "youtube:dQw4w9WgXcQ": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ?si=abc", "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "https://www.youtube.com/shorts/dQw4w9WgXcQ", "https://www.youtube.com/embed/dQw4w9WgXcQ"],
    "github:ollama/ollama": ["https://github.com/ollama/ollama", "https://github.com/Ollama/Ollama/", "https://github.com/ollama/ollama.git", "https://www.github.com/ollama/ollama?tab=readme-ov-file"],
  };
  for (const [key, urls] of Object.entries(same)) for (const url of urls) assert.equal(identityKeyForUrl(url), key, url);
});

test("GitHub releases and commits, and lookalike hosts, are not the repository's identity", () => {
  for (const url of ["https://github.com/ollama/ollama/releases/tag/v0.5.0", "https://github.com/ollama/ollama/commit/abc123", "https://github.com/ollama", "https://notgithub.com/ollama/ollama", "https://arxiv.org.example.org/abs/2410.12345", "https://arxiv.org/list/cs.AI/recent", "https://www.youtube.com/watch", "https://www.youtube.com/watch?v=short"]) {
    assert.equal(identityKeyForUrl(url), `url:${normalizeUrl(url)}`, url);
  }
});
