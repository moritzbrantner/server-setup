// DNSControl is the canonical DNS integration for server-setup.
// Keep provider credentials in creds.json (ignored by git) and declare zones here.
// The no-op registrar intentionally limits this stack to DNS-record management.
//
// var REG_NONE = NewRegistrar("none");
//
// Porkbun example:
// var DSP_PORKBUN = NewDnsProvider("porkbun");
// D("example.com", REG_NONE, DnsProvider(DSP_PORKBUN), A("@", "203.0.113.10"));
//
// Namecheap example:
// var DSP_NAMECHEAP = NewDnsProvider("namecheap");
// D("example.net", REG_NONE, DnsProvider(DSP_NAMECHEAP), A("@", "203.0.113.10"));
