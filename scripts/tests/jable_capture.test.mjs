import assert from 'node:assert/strict';
import { test } from 'node:test';
import { publicModelUrl, publicAvatarUrl, projectModels, listingPage } from '../capture_jable_models.mjs';

test('model and pagination URLs are distinguished and restricted', () => {
  assert.equal(publicModelUrl('https://jable.tv/models/example/'), 'https://jable.tv/models/example/');
  for (const url of ['https://jable.tv/models/2/', 'https://evil.example/models/a/', 'https://jable.tv:8080/models/a/', 'https://jable.tv/models/a/?token=secret']) assert.equal(publicModelUrl(url), null);
  assert.equal(listingPage('https://jable.tv/models/214/'), 214);
  assert.equal(listingPage('https://jable.tv/models/'), 1);
  assert.equal(listingPage('https://jable.tv/models/example/'), null);
  assert.equal(listingPage('https://jable.tv/models/?mode=async'), null);
});

test('only observed portrait-path assets are allowed', () => {
  assert.equal(publicAvatarUrl('https://assets-cdn.jable.tv/contents/models/1/name.jpg'), 'https://assets-cdn.jable.tv/contents/models/1/name.jpg');
  for (const url of ['https://assets-cdn.jable.tv/contents/videos/1.jpg', 'https://evil.example/contents/models/1.jpg', 'https://assets-cdn.jable.tv/contents/models/1.jpg?secret=1']) assert.equal(publicAvatarUrl(url), null);
});

test('projection escapes text, deduplicates and records missing portraits', () => {
  const result = projectModels([
    {url:'https://jable.tv/models/a/',name:'A <script>',image:'https://assets-cdn.jable.tv/contents/models/1/a.jpg',work_count:3},
    {url:'https://jable.tv/models/a/',name:'A <script>',work_count:3},
    {url:'https://jable.tv/models/b/',name:'B',image:'https://evil.example/a.jpg'},
  ]);
  assert.equal(result.models.length, 2);
  assert.equal(result.models.filter(row=>row.avatar).length, 1);
  assert.equal(result.rejected.length, 1);
  assert.ok(result.html.includes('A &lt;script&gt;'));
  assert.ok(result.html.includes('3 部影片'));
  assert.ok(!result.html.includes('evil.example'));
});
