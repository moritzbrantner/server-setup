// DNSControl is the canonical DNS integration for server-setup.
// Keep provider credentials in creds.json (ignored by git) and declare zones here.
//
// Porkbun example:
// var REG_PORKBUN = NewRegistrar("porkbun");
// var DSP_PORKBUN = NewDnsProvider("porkbun");
// D("example.com", REG_PORKBUN, DnsProvider(DSP_PORKBUN), A("@", "203.0.113.10"));
//
// Namecheap example:
// var REG_NAMECHEAP = NewRegistrar("namecheap");
// var DSP_NAMECHEAP = NewDnsProvider("namecheap");
// D("example.net", REG_NAMECHEAP, DnsProvider(DSP_NAMECHEAP), A("@", "203.0.113.10"));
