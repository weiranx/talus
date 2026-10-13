import ActivitiesTabs from "@/components/ActivitiesTabs";
import Heatmap from "@/components/Heatmap";

export default function HeatmapPage() {
  return (
    <>
      <header className="page-head">
        <div>
          <h1 className="title">Activities</h1>
          <div className="muted" style={{ paddingBottom: 6 }}>Every route you have recorded, brighter where you go often</div>
        </div>
        <ActivitiesTabs current="heatmap" />
      </header>
      <Heatmap />
    </>
  );
}
