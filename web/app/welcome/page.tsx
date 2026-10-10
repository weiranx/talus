import Link from "next/link";
import { redirect } from "next/navigation";
import EngineDown from "@/components/EngineDown";
import GarminCard from "@/components/GarminCard";
import ReadinessSettings from "@/components/ReadinessSettings";
import SourceCard from "@/components/SourceCard";
import ThresholdsCard from "@/components/ThresholdsCard";
import UploadCard from "@/components/UploadCard";
import CoachConsent from "@/components/onboarding/CoachConsent";
import ImportStep from "@/components/onboarding/ImportStep";
import SkipSetup from "@/components/onboarding/SkipSetup";
import StepNav from "@/components/onboarding/StepNav";
import Stepper from "@/components/onboarding/Stepper";
import { isStep } from "@/components/onboarding/steps";
import { engine, EngineError } from "@/lib/api";
import type { Onboarding, OnboardingStep, Settings } from "@/lib/types";

export const dynamic = "force-dynamic";

const HEAD: Record<OnboardingStep, { title: string; lede: string }> = {
  connect: {
    title: "Connect your training",
    lede: "Talus tracks four loads from your runs and hikes: cardio, climb, descent and distance, in miles and feet. Each one is "
      + "compared with your own normal, so it starts from your history. Connect one source now; you can add more later in Settings.",
  },
  import: { title: "Import your history", lede: "Pick how far back to read. The import runs in the background, so you can keep going." },
  thresholds: {
    title: "Heart rate thresholds",
    lede: "These set your zones and how hard each run counts for cardio load. Use the estimate from your runs, or enter numbers from a test.",
  },
  sleep: { title: "How much sleep you need", lede: "Readiness reads last night and your 7-day average against this." },
  coach: { title: "The AI coach", lede: "Choose whether an AI model writes the words around your numbers." },
  tour: { title: "Your morning on Today", lede: "Three things on Today answer one question: train, ease off, or rest?" },
};

export default async function WelcomePage({ searchParams }: { searchParams: Promise<{ step?: string }> }) {
  let ob: Onboarding, s: Settings;
  try {
    [ob, s] = await Promise.all([engine<Onboarding>("/api/onboarding"), engine<Settings>("/api/settings")]);
  } catch (e) {
    return <EngineDown message={e instanceof EngineError ? e.message : "Could not reach the engine."} />;
  }
  const asked = (await searchParams).step;
  // Keep the step in the URL, so a refresh after an upload or save stays on it.
  if (!isStep(asked)) redirect(`/welcome?step=${ob.next ?? "tour"}`);
  const step: OnboardingStep = asked;
  const state = Object.fromEntries(ob.steps.map((x) => [x.key, x]));
  const importing = ob.import?.state === "running";

  return (
    <div style={{ maxWidth: 760, margin: "0 auto", width: "100%", display: "flex", flexDirection: "column", gap: 16 }}>
      <header className="page-head" style={{ alignItems: "flex-start" }}>
        <div>
          <div className="eyebrow">Set up Talus</div>
          <h1 className="title">{HEAD[step].title}</h1>
        </div>
        <div style={{ paddingTop: 6 }}><SkipSetup started={Boolean(ob.started) || !ob.needed} /></div>
      </header>
      <Stepper ob={ob} step={step} />
      <p style={{ margin: 0 }}>
        {step === "import" && !ob.sources.intervals && !ob.sources.garmin ? "Your history comes from the files you upload." : HEAD[step].lede}
      </p>

      {step === "connect" ? (
        <>
          <SourceCard source={s.source} lastSync={s.sources.intervals.last_sync} returnTo="/welcome?step=connect" hideSync />
          {s.sources.garmin.available ? <GarminCard garmin={s.sources.garmin} /> : null}
          <UploadCard upload={s.sources.upload} />
          <StepNav step="connect" skippable={false} markDone={false}
            blocked={state.connect.done ? null : "Connect intervals.icu or upload some files to go on."} />
        </>
      ) : null}

      {step === "import" ? (
        <>
          <ImportStep ob={ob} />
          <StepNav step="import" skippable={false} markDone={false}
            blocked={state.import.done || importing ? null : ob.sources.intervals || ob.sources.garmin
              ? "Start the import to go on." : "Upload files on the Connect step to go on."} />
        </>
      ) : null}

      {step === "thresholds" ? (
        <>
          {importing ? <div className="muted">Your history is still importing. The estimate fills in as runs arrive.</div> : null}
          <ThresholdsCard current={s.thresholds} inferred={s.inferred} restingHint={ob.resting_hint} />
          {!s.thresholds ? (
            <div className="muted">
              You can skip this. Today stays empty until thresholds are set, and asks for them. A provisional AeT is labeled as one everywhere.
            </div>
          ) : null}
          <StepNav step="thresholds" skippable={!s.thresholds} markDone={false}
            blocked={s.thresholds ? null : "Save thresholds, or skip for now."} />
        </>
      ) : null}

      {step === "sleep" ? (
        <>
          <ReadinessSettings readiness={s.readiness} />
          <StepNav step="sleep" skippable={false}
            nextLabel={state.sleep.done ? undefined : `Keep ${s.readiness.sleep_need_h} h, next: coach`} />
        </>
      ) : null}

      {step === "coach" ? (
        <>
          <CoachConsent ob={ob} />
          <div className="actions"><Link href="/welcome?step=sleep">Back</Link></div>
        </>
      ) : null}

      {step === "tour" ? (
        <>
          <section className="card">
            <h2 className="label">Today&apos;s call</h2>
            <div>
              Go, ease off, or rest, with the session to do. It comes from your four loads, last night&apos;s sleep and heart
              rate, and your check-in. {ob.coach.enabled ? "The coach writes the reason in plain words." : "With the coach off, the reason is a plain rule-based line."}
            </div>
          </section>
          <section className="card">
            <h2 className="label">Morning check-in</h2>
            <div>
              Three taps: leg soreness, energy, and any pain. It sharpens the legs reading, which your watch can&apos;t see.
              Pain above 3 of 5 always means rest. Your first check-in is tomorrow morning.
            </div>
          </section>
          <section className="card">
            <h2 className="label">Readiness score</h2>
            <div>
              One number from 0 to 100: Fresh is 85 and up, Ready 67 and up, Ease off 34 and up, and below that Rest. Tap a
              number on Today to see how it was worked out.
            </div>
          </section>
          <section className="card">
            <h2 className="label">What happens next</h2>
            <ul className="method-list">
              {importing ? <li>Your history is still importing. Today fills in as it lands.</li> : null}
              {!s.thresholds ? <li>Today needs heart rate thresholds before it can score anything. <Link href="/welcome?step=thresholds">Set them now</Link>.</li> : null}
              <li>Your normal for each load builds over your first 4 weeks of history, and is marked as building until then.</li>
              <li>Add a race or mountain day on <Link href="/plan">Plan</Link> to get a week-by-week plan.</li>
            </ul>
          </section>
          <StepNav step="tour" skippable={false} nextLabel="Go to Today" finish />
        </>
      ) : null}
    </div>
  );
}
