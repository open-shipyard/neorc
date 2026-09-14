import { Layout } from "./components/Layout";
import { FlowPage } from "./pages/FlowPage";
import { FlowsPage } from "./pages/FlowsPage";
import { RunPage } from "./pages/RunPage";
import { RunsPage } from "./pages/RunsPage";
import { useLiveEvents } from "./queries";
import { href, useRoute } from "./router";

export function App() {
  const route = useRoute();
  useLiveEvents();
  return (
    <Layout route={route}>
      <Page route={route} />
    </Layout>
  );
}

function Page({ route }: { route: ReturnType<typeof useRoute> }) {
  switch (route.name) {
    case "runs":
      return <RunsPage flow={route.flow} status={route.status} />;
    case "flows":
      return <FlowsPage />;
    // Keyed, so nothing typed or half-confirmed on one page carries to another.
    case "flow":
      return (
        <FlowPage
          key={`${route.flow}@${route.version ?? "latest"}`}
          flow={route.flow}
          version={route.version}
        />
      );
    case "run":
      return <RunPage key={route.id} id={route.id} />;
    case "unknown":
      return (
        <p role="alert">
          Nothing at <code>{route.hash}</code>.{" "}
          <a href={href({ name: "runs" })}>Runs</a>
        </p>
      );
  }
}
