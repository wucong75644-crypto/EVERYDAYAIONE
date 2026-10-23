import { useLayoutEffect, useMemo } from 'react';
import type { ReactNode } from 'react';
import { SurfaceMotionContext, SurfaceOwner } from './useCanvasSurfaceMotion';
export function CanvasSurfaceMotion({ identity, reduced, children }: { identity: string; reduced: boolean; children: ReactNode }) {
  const owner = useMemo(() => new SurfaceOwner(identity), [identity]);
  useLayoutEffect(() => { owner.start(); return () => owner.dispose(); }, [owner]);
  useLayoutEffect(() => owner.preference(reduced), [reduced, owner]);
  return <SurfaceMotionContext.Provider value={owner}>{children}</SurfaceMotionContext.Provider>;
}
