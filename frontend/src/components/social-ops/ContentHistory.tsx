import { SocialAssetPreview } from "./CardStudio";
import type { Draft } from "../../services/socialOps";

export default function ContentHistory({ rows }: { rows: unknown[] }) {
  return (
    <div className="space-y-3">
      {rows.map((value, index) => {
        const row = value as {
          revision?: number;
          data?: Draft["data"];
          manifest?: unknown;
          checks?: unknown;
        };
        if (!row.data || typeof row.data.title !== "string")
          return (
            <pre key={index} className="text-xs whitespace-pre-wrap break-all">
              {JSON.stringify(value, null, 2)}
            </pre>
          );
        return (
          <details
            key={row.revision ?? index}
            className="border rounded-xl p-4"
          >
            <summary className="font-medium">
              v{row.revision} · {row.data.title} ·{" "}
              {row.data.assets?.length ?? 0}张图片
            </summary>
            <p className="whitespace-pre-wrap my-3">{row.data.body}</p>
            <p className="text-sm text-accent">
              {row.data.tags?.map((t) => "#" + t).join(" ")}
            </p>
            <div className="grid grid-cols-3 gap-3 mt-3">
              {row.data.assets?.map((a) => (
                <SocialAssetPreview key={a.id} id={a.id} />
              ))}
            </div>
            <details className="mt-3">
              <summary>检查与制作依据</summary>
              <pre className="text-xs whitespace-pre-wrap break-all">
                {JSON.stringify(
                  { checks: row.checks, manifest: row.manifest },
                  null,
                  2,
                )}
              </pre>
            </details>
          </details>
        );
      })}
    </div>
  );
}
