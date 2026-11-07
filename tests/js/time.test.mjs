import test from "node:test";
import assert from "node:assert/strict";
import { src } from "./helpers.mjs";
const T = await import(src("time.js"));

test("rangeStart: NdaysAgo is a date, floored at one day back", () => {
  assert.equal(T.rangeStart(30), "1daysAgo");        // never 0daysAgo: today can be an hour old
  assert.equal(T.rangeStart(60), "1daysAgo");
  assert.equal(T.rangeStart(1440), "1daysAgo");
  assert.equal(T.rangeStart(7 * 1440), "6daysAgo");  // 6daysAgo..today is seven calendar days
  assert.equal(T.rangeStart(90 * 1440), "89daysAgo");
});

test("rangeHours is whole buckets, at least one", () => {
  assert.equal(T.rangeHours(30), 1);
  assert.equal(T.rangeHours(60), 1);
  assert.equal(T.rangeHours(1440), 24);
});

test("the window is anchored to the latest dateHour, never a clock", () => {
  assert.deepEqual(T.anchoredWindow("2026110403", 1440), { start: "2026110304", end: "2026110403" });
  assert.deepEqual(T.anchoredWindow("2026110403", 60), { start: "2026110403", end: "2026110403" });
  assert.equal(T.anchoredWindow(null, 60), null);
  assert.deepEqual(T.previousWindow({ start: "2026110304", end: "2026110403" }),
                   { start: "2026110204", end: "2026110303" });
  assert.equal(T.hoursIn({ start: "2026110322", end: "2026110401" }).length, 4);
});

test("label arithmetic crosses months and years", () => {
  assert.equal(T.shiftDH("2026123123", 1), "2027010100");
  assert.equal(T.shiftDay("20260301", -1), "20260228");
});

test("buckets: weeks start Monday, a summed day keeps unknown secondary values null", () => {
  assert.equal(T.bucketStart("2026110415", "week"), "2026110200");   // Wed -> Mon
  const pts = [{ ts: "2026110400", value: 2, baseline: 1, secondary: null },
               { ts: "2026110401", value: 3, baseline: 1, secondary: null },
               { ts: "2026110500", value: 4, baseline: 2, secondary: 5 }];
  const days = T.bucketPoints(pts, "day");
  assert.deepEqual(days.map(d => [d.ts, d.value, d.secondary]), [["2026110400", 5, null], ["2026110500", 4, 5]]);
  assert.equal(T.bucketFits("day", 1440), false);
  assert.equal(T.bucketFits("day", 2880), true);
  assert.equal(T.bucketFits("week", 10080), false);
});

test("realtime points name no timestamp", () => {
  assert.equal(T.pointLabel({ minutesAgo: 0 }), "now");
  assert.equal(T.pointLabel({ minutesAgo: 7 }), "7 min ago");
  assert.equal(T.tileRangeLabel(60), "latest reported hour");
});
