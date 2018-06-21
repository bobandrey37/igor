#!/usr/bin/env python
import sys
import igor
import os
import os.path
import shutil
import getpass
import tempfile
import subprocess
import ConfigParser
import re

class SSLConfigParser(ConfigParser.RawConfigParser):
    """SSL Configuration files are case-dependent"""
    
    SECTCRE = re.compile(
        r'\[\s*'                                 # [
        r'(?P<header>[^]\s]+)'                  # very permissive!
        r'\s*\]'                                 # ]
        )

    def optionxform(self, optionstr):
        return optionstr

USAGE="""
Usage: %s command [args]

Initialize or use igor Certificate Authority.
"""

class IgorCA:
    def __init__(self, argv0):
        self.argv0 = argv0
        # Find username even when sudoed
        username = os.environ.get("SUDO_USER", getpass.getuser())
        # Igor package source directory
        self.igorDir = os.path.dirname(igor.__file__)
        #
        # Default self.database directory, CA directory and key/cert for signing.
        #
        self.database = os.path.join(os.path.expanduser('~'+username), '.igor')
        self.caDatabase = os.path.join(self.database, 'ca')
        self.intKeyFile = os.path.join(self.caDatabase, 'intermediate', 'private', 'intermediate.key.pem')
        self.intCertFile = os.path.join(self.caDatabase, 'intermediate', 'certs', 'intermediate.cert.pem')
        self.intAllCertFile = os.path.join(self.caDatabase, 'intermediate', 'certs', 'ca-chain.cert.pem')
        self.intConfigFile = os.path.join(self.caDatabase, 'intermediate', 'openssl.cnf')

    def runSSLCommand(self, *args):
        args = ('openssl',) + args
        print >>sys.stderr, '+', ' '.join(args)
        sts = subprocess.call(args)
        if sts != 0:
            print >>sys.stderr, '%s: openssl returned status %d' % (self.argv0, sts)
            return False
        return True
    
    def main(self, command, args):
        if command == 'help':
            self.cmd_help()
            sys.exit(0)

        if not os.path.exists(self.database):
            print >>sys.stderr, "%s: No Igor self.database at %s" % (self.argv0, self.database)
            sys.exit(1)
        
        if command == 'initialize':
            ok = self.cmd_initialize()
            if not ok:
                sys.exit(1)
            sys.exit(0)
        #
        # All other commands require the self.database to exist and be populated with the keys
        #
        if not os.path.exists(self.caDatabase):
            print >>sys.stderr, "%s: No Igor CA self.database at %s" % (self.argv0, self.caDatabase)
            sys.exit(1)
        if not (os.path.exists(self.intKeyFile) and os.path.exists(self.intCertFile) and os.path.exists(self.intAllCertFile)):
            print >>sys.stderr, "%s: Intermediate key, certificate and chain don't exist in %s" % (self.argv0, self.caDatabase)
            sys.exit(1)
        
    
        if not hasattr(self, 'cmd_' + command):
            print >> sys.stderr, '%s: Unknown command "%s". Use help for help.' % (self.argv0, command)
            sys.exit(1)
        handler = getattr(self, 'cmd_' + command)
        ok = handler(*args)
        if not ok:
            sys.exit(1)
        sys.exit(0)
    
    def get_distinguishedName(self):
        """Helper that returns DN in key-value dict"""
        fp = subprocess.Popen(['openssl', 'x509', '-in', self.intCertFile, '-noout', '-subject'], stdout=subprocess.PIPE)
        data, _ = fp.communicate()
        if not data.startswith('subject='):
            print >>sys.stderr, '%s: unexpected openssl x509 output: %s' % (self.argv0, data)
            sys.exit(1)
        data = data[8:]
        data = data.strip()
        dataItems = data.split('/')
        rv = {}
        for di in dataItems:
            if not di: continue
            diSplit = di.split('=')
            k = diSplit[0]
            v = '='.join(diSplit[1:])
            rv[k] = v
        return rv
        
    def cmd_help(self, *args):
        """Show list of available commands"""
        print USAGE % self.argv0
        for name in dir(self):
            if not name.startswith('cmd_'): continue
            handler = getattr(self, name)
            print '%-10s\t%s' % (name[4:], handler.__doc__)
    
    def cmd_initialize(self):
        """create CA infrastructure, root key and certificate and intermediate key and certificate"""
        if os.path.exists(self.intKeyFile) and os.path.exists(self.intCertFile) and os.path.exists(self.intAllCertFile):
            print >>sys.stderr, '%s: Intermediate key and certificate already exist in %s' % (self.argv0, self.caDatabase)
            return False
        #
        # Create infrastructure if needed
        #
        if not os.path.exists(self.caDatabase):
            # Old igor, probably: self.caDatabase doesn't exist yet
            print
            print '=============== Creating CA directories and infrastructure'
            src = os.path.join(self.igorDir, 'igorDatabase.empty')
            caSrc = os.path.join(src, 'ca')
            print >>sys.stderr, '%s: Creating %s' % (self.argv0, caSrc)
            shutil.copytree(caSrc, self.caDatabase)
        #
        # Create openssl.cnf files from openssl.cnf.in
        #
        for caGroup in ('root', 'intermediate'):
            print
            print '=============== Creating config for', caGroup
            caGroupDir = os.path.join(self.caDatabase, caGroup)
            caGroupConf = os.path.join(caGroupDir, 'openssl.cnf')
            caGroupConfIn = os.path.join(caGroupDir, 'openssl.cnf.in')
            if os.path.exists(caGroupConf):
                print >> sys.stderr, '%s: %s already exists' % (self.argv0, caGroupConf)
                return False
            data = open(caGroupConfIn).read()
            data = data.replace('%INSTALLDIR%', self.database)
            open(caGroupConf, 'w').write(data)
        #
        # Create root key and certificate
        #
        rootKeyFile = os.path.join(self.caDatabase, 'root', 'private', 'ca.key.pem')
        rootCertFile = os.path.join(self.caDatabase, 'root', 'certs', 'ca.cert.pem')
        rootConfigFile = os.path.join(self.caDatabase, 'root', 'openssl.cnf')
        if  os.path.exists(rootKeyFile) and os.path.exists(rootCertFile) and os.path.exists(rootConfigFile):
            print
            print '=============== Root key and certificate already exist'
        else:
            print
            print '=============== Creating root key and certificate'
            ok = self.runSSLCommand('genrsa', '-aes256', '-out', rootKeyFile, '4096')
            if not ok:
                return False
            os.chmod(rootKeyFile, 0400)
            ok = self.runSSLCommand('req', 
                '-config', rootConfigFile, 
                '-key', rootKeyFile, 
                '-new', 
                '-x509', 
                '-days', '7300', 
                '-sha256', 
                '-extensions', 'v3_ca', 
                '-out', rootCertFile
                )
            if not ok:
                return False
            os.chmod(rootCertFile, 0400)
        ok = self.runSSLCommand('x509', '-noout', '-text', '-in', rootCertFile)
        if not ok:
            return False
        #
        # Create intermediate key, CSR and certificate
        #
        print
        print '=============== Creating intermediate key and certificate'
        ok = self.runSSLCommand('genrsa', '-out', self.intKeyFile, '4096')
        os.chmod(self.intKeyFile, 0400)
        if not ok:
            return False
        intCsrFile = os.path.join(self.caDatabase, 'intermediate', 'certs', 'intermediate.csr.pem')
        ok = self.runSSLCommand('req', 
            '-config', self.intConfigFile, 
            '-key', self.intKeyFile, 
            '-new', 
            '-sha256', 
            '-out', intCsrFile
            )
        if not ok:
            return False
        ok = self.runSSLCommand('ca',
            '-config', rootConfigFile,
            '-extensions', 'v3_intermediate_ca',
            '-days', '3650',
            '-notext',
            '-md', 'sha256',
            '-in', intCsrFile,
            '-out', self.intCertFile
            )
        if not ok:
            return False
        os.chmod(self.intCertFile, 0400)
        #
        # Verify the intermediate certificate
        #
        ok = self.runSSLCommand('verify',
            '-CAfile', rootCertFile,
            self.intCertFile
            )
        if not ok:
            return False
        #
        # Concatenate
        #
        ofp = open(self.intAllCertFile, 'w')
        ofp.write(open(self.intCertFile).read())
        ofp.write(open(rootCertFile).read())
        ofp.close()
        #
        # And finally print the chained file
        #
        ok = self.runSSLCommand('x509', '-noout', '-text', '-in', self.intAllCertFile)
        if not ok:
            return False
    
        return True

    def cmd_self(self, *allNames):
        """Create a server key and certificate for Igor itself, and sign it with the intermediate Igor CA key"""
        if len(allNames) < 1:
            print >>sys.stderr, '%s: self requires ALL names (commonName first) as arguments' % self.argv0
            print >> sys.stderr, 'for example: %s self igor.local localhost 127.0.0.1' % self.argv0
            return False
        # Construct commonName and subjectAltNames
        commonName = allNames[0]
        altNames = map(lambda x: 'DNS:' + x, allNames)
        altNames = ','.join(altNames)

        igorKeyFile = os.path.join(self.database, 'igor.key')
        igorCsrFile = os.path.join(self.database, 'igor.csr')
        igorCsrConfigFile = os.path.join(self.database, 'igor.csrconfig')
        igorCertFile = os.path.join(self.database, 'igor.crt')
        if os.path.exists(igorKeyFile) and os.path.exists(igorCertFile):
            print >>sys.stderr, '%s: igor.key and igor.crt already exist in %s' % (self.argv0, self.database)
            return False
        #
        # Create key
        #
        ok = self.runSSLCommand('genrsa', '-out', igorKeyFile, '2048')
        if not ok:
            return False
        os.chmod(igorKeyFile, 0400)
        #
        # Create CSR config file
        #
        cfg = SSLConfigParser(allow_no_value=True)
        cfg.readfp(open(self.intConfigFile), self.intConfigFile)
        
        # Get distinghuished name info and put in the config file
        dnDict = self.get_distinguishedName()
        dnDict['CN'] = commonName
        cfg.remove_section('req_distinguished_name')
        cfg.add_section('req_distinguished_name')
        for k, v in dnDict.items():
            cfg.set('req_distinguished_name', k, v)
        # Set to non-interactive
        cfg.set('req', 'prompt', 'no')
        # Add the subjectAltName
        cfg.set('req', 'req_extensions', 'req_ext')
        cfg.add_section('req_ext')
        cfg.set('req_ext', 'subjectAltName', altNames)
        # And add subjectAltName to server_cert section
        cfg.set('server_cert', 'subjectAltName', altNames)
        # Write to CSR config file
        ofp = open(igorCsrConfigFile, 'w')
        cfg.write(ofp)
        ofp.close()
        #
        # Create CSR
        #
        ok = self.runSSLCommand('req',
            '-config', igorCsrConfigFile,
            '-key', igorKeyFile,
            '-new',
            '-sha256',
            '-out', igorCsrFile
            )
        if not ok:
            return False
        #
        # Sign CSR
        #
        ok = self.runSSLCommand('ca',
            '-config', igorCsrConfigFile,
            '-extensions', 'server_cert',
            '-days', '3650',
            '-notext',
            '-md', 'sha256',
            '-in', igorCsrFile,
            '-out', igorCertFile
            )
        if not ok:
            return False
        # Verify it
        ok = self.runSSLCommand('x509', '-noout', '-text', '-in', igorCertFile)
        if not ok:
            return False
        return True
                    
    def cmd_getRoot(self):
        """Returns the signing certificate chain (for installation in browser or operating system)"""
        sys.stdout.write(open(self.intAllCertFile).read())
        return True
        
    def cmd_sign(self):
        """Sign a Certificate Signing Request. Not yet implemented."""
        return False
        
    def cmd_gen(self):
        """Generate a key and certificate. Not yet implemented."""
        return False

    def cmd_list(self):
        """Return list of certificates signed. Not yet implemented."""
        return False
        
def main():
    m = IgorCA(sys.argv[0])
    if len(sys.argv) < 2:
        return m.main('help', [])
    return m.main(sys.argv[1], sys.argv[2:])
    
if __name__ == '__main__':
    main()
