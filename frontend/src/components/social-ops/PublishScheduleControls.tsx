export const publishStates: Record<string, string> = {
  awaiting_confirmation: "待最终确认",
  scheduled: "已排期",
  preparing: "准备派发（尚可取消）",
  waiting_budget: "等待账号额度/访问窗口",
  paused: "暂停，需处理并重新确认",
  dispatching: "派发中，无法保证撤回",
  submitted: "已提交待核验",
  verified: "已核验（人工）",
  uncertain: "结果不确定，请核验，禁止重发",
  failed: "失败",
  cancelled: "已取消",
  expired: "授权过期，未补发",
  invalidated: "确认已失效",
  not_published: "人工核验未发布",
};
export const scheduleZones = [
  "Asia/Shanghai",
  "UTC",
  "America/New_York",
  "Europe/London",
  "Asia/Tokyo",
];
export function zonedTime(value: string, zone: string) {
  // Resolve a wall clock to exactly one instant. Reject DST gaps/overlaps.
  const base = Date.parse(value + "Z");
  if (!Number.isFinite(base)) throw new Error("请选择有效的日期和时间");
  const format = new Intl.DateTimeFormat("sv-SE", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const matches: number[] = [];
  for (let delta = -14 * 60; delta <= 14 * 60; delta += 15) {
    const instant = base + delta * 60000;
    if (format.format(instant).replace(" ", "T") === value)
      matches.push(instant);
  }
  if (matches.length !== 1)
    throw new Error("该时区时间不存在或夏令时重复，请选择其他时间");
  return new Date(matches[0]).toISOString();
}
export function ScheduleFields({
  time,
  zone,
  grace,
  onChange,
}: {
  time: string;
  zone: string;
  grace: number;
  onChange: (time: string, zone: string, grace: number) => void;
}) {
  return (
    <div className="grid sm:grid-cols-3 gap-3">
      <label>
        发布日期与时间
        <input
          aria-label="发布日期与时间"
          type="datetime-local"
          value={time}
          onChange={(e) => onChange(e.target.value, zone, grace)}
          className="border rounded-lg p-2 w-full"
        />
      </label>
      <label>
        时区
        <select
          aria-label="发布时区"
          value={zone}
          onChange={(e) => onChange(time, e.target.value, grace)}
          className="border rounded-lg p-2 w-full"
        >
          {scheduleZones.map((z) => (
            <option key={z}>{z}</option>
          ))}
        </select>
      </label>
      <label>
        允许迟到（秒）
        <input
          aria-label="允许迟到秒数"
          type="number"
          min={60}
          max={3600}
          value={grace}
          onChange={(e) => onChange(time, zone, Number(e.target.value))}
          className="border rounded-lg p-2 w-full"
        />
      </label>
      <p className="sm:col-span-3 text-sm text-amber-700">
        默认仅允许60秒调度窗口；超过窗口过期不补发。修改任何排期设置后必须重新审核确认。平台审核状态另行核验。
      </p>
    </div>
  );
}
