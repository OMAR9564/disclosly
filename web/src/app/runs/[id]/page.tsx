import { Suspense } from "react";

import { RunView } from "@/components/RunView";

/** Reload-safe run view; the run id is read on the client, so it streams in after prerender. */
export default function RunPage() {
  return (
    <Suspense fallback={null}>
      <RunView />
    </Suspense>
  );
}
