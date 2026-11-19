import { useEffect, useState } from "react";
import { read } from "./scopedApi";
export function useScopedResource<T>(path: string | null, revision = 0) {
  const key = JSON.stringify([path, revision]);
  const [state, setState] = useState<{key: string; data: T | null; loading: boolean; error: string}>({ key, data: null, loading: Boolean(path), error: "" });
  useEffect(() => {
    let active = true;
    setState({ key, data: null, loading: Boolean(path), error: "" });
    if (path) read<T>(path).then((data) => { if (active) setState({key, data, loading: false, error: ""}); }).catch((error) => { if (active) setState({key, data: null, loading: false, error: error instanceof Error ? error.message : "Данные недоступны"}); });
    return () => {active = false;};
  }, [path, revision]);
  return state.key === key ? state : {data: null, loading: Boolean(path), error: ""};
}
