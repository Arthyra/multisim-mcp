"""Expose the pinned COM backend's existing multi-output worker method."""

from multisim_mcp import server


if server.__version__ == "1.3.1":
    @server.mcp.tool()
    def run_transient_outputs(
        output_names: list[str], sample_rate: float, num_samples: int, duration: float,
        timeout: float = 30.0, max_points: int = 2000,
    ) -> dict:
        """Collect all requested output channels from one upstream transient run."""
        return server.client.run_transient_outputs(
            output_names, sample_rate, num_samples, duration, timeout, max_points,
        )


if __name__ == "__main__":
    server.main()
