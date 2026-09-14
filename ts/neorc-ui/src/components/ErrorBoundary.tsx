import { Component, type ErrorInfo, type ReactNode } from "react";

/** The last line of defence: a render error shows here, not as a blank page. */
export class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("the page failed to render", error, info.componentStack);
  }

  render() {
    if (this.state.error !== null) {
      return (
        <main>
          <p role="alert">
            The page failed to render: {this.state.error.message}.{" "}
            <a href="#/runs" onClick={() => this.setState({ error: null })}>
              Runs
            </a>
          </p>
        </main>
      );
    }
    return this.props.children;
  }
}
