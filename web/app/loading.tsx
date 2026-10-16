import { Skel, SkelLines, SkelPage } from "@/components/Loading";

export default function TodayLoading() {
  return (
    <SkelPage label="Loading today">
      <div className="today-workspace">
        <div className="today-primary">
          <header className="today-header"><div><Skel w={150} h={13} /><Skel w={110} h={38} style={{ marginTop: 8 }} /></div></header>
          <section className="today-session" style={{ minHeight: 280 }}>
            <Skel w={130} h={12} />
            <Skel w="70%" h={32} style={{ marginBlock: 20 }} />
            <SkelLines n={2} h={15} />
            <Skel w={150} h={42} r={9} style={{ marginTop: 26 }} />
          </section>
          <section className="today-load">
            <Skel w={260} h={22} /><Skel w="70%" h={13} style={{ marginTop: 10 }} />
            <div className="today-load-rows">{[0, 1, 2].map((i) => <div className="today-load-row" key={i}>
              <Skel w={60} h={14} /><Skel h={8} r={4} /><Skel w={40} h={14} />
            </div>)}</div>
          </section>
          <section className="today-week"><Skel w={100} h={22} /><div className="today-week-totals">{[0, 1, 2].map((i) => <div key={i}><Skel w="75%" h={26} /><Skel w={65} h={12} style={{ marginTop: 10 }} /></div>)}</div></section>
        </div>
        <aside className="today-rail">
          <section><Skel w={150} h={22} />{[0, 1].map((i) => <div className="today-readiness-row" key={i}><SkelLines n={2} /><Skel w={58} h={20} /></div>)}</section>
          <section className="today-checkin"><Skel w={160} h={22} /><SkelLines n={2} />{[0, 1, 2].map((i) => <Skel key={i} h={42} r={8} />)}</section>
        </aside>
      </div>
    </SkelPage>
  );
}
