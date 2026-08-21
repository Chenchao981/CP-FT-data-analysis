import * as echarts from "echarts";
import { useEffect, useRef } from "react";

interface Props {
  option: echarts.EChartsOption;
  className?: string;
}

export function EChart({ option, className }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option, true);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [option]);

  return <div ref={ref} className={className ?? "chart-canvas"} />;
}
