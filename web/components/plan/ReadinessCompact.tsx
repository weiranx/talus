import Readiness from "./Readiness";
import type { PlanPage, ReadinessEval } from "@/lib/types";

// Keep the accepted-plan summary and draft review on the same units and benchmarks.
export default function ReadinessCompact(props: {
  r: NonNullable<PlanPage["readiness"]>; projected: ReadinessEval | null; note: string; projectionCurrent?: boolean;
}) {
  return <Readiness {...props} />;
}
