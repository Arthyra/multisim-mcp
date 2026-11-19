# Third-party notices

- `y-wi/multisim-native-mcp` v0.5.2 runs as an external MCP subprocess. MIT, copyright (c) 2026 y-wi. [Upstream license](https://github.com/y-wi/multisim-native-mcp/blob/v0.5.2/LICENSE)
- `yxy050208/multisim-mcp` v1.3.1 runs as an external MCP subprocess. MIT, copyright (c) 2026 Multisim MCP contributors. [Upstream license](https://github.com/yxy050208/multisim-mcp/blob/v1.3.1/LICENSE)
- The COM backend optionally uses `electronics-workbench-decoder` v0.2.0 under ISC for `.ms14` XML conversion. [COM backend notices](https://github.com/yxy050208/multisim-mcp/blob/v1.3.1/THIRD_PARTY_NOTICES.md)
- The gateway depends on the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) under MIT.
- NI Multisim is separate software. Users install and license it independently. Multisim component databases, NI program files, extracted models and circuit templates are excluded from this repository.

The native backend uses Frida and non-public Multisim interfaces. Review the license and any additional terms applicable to your NI installation before using or distributing an integration that depends on those interfaces. Section 1.B of the [NI General Purpose Software License Agreement](https://www.ni.com/en/about-ni/legal/software-license-agreement.html/) addresses reverse engineering and discovery of non-public interfaces. NI documents where to find [additional installed license terms](https://knowledge.ni.com/KnowledgeArticleDetails?id=kA00Z0000019X3KSAU).
