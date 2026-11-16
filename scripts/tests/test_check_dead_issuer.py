import importlib.util, pathlib
spec = importlib.util.spec_from_file_location('cdi', pathlib.Path(__file__).parents[1] / 'check-dead-issuer.py')
cdi = importlib.util.module_from_spec(spec); spec.loader.exec_module(cdi)

def test_recommendation_fails():
    assert cdi.violations('<p>Best for casual spenders: Cypher. Simple tiers.</p>')

def test_closed_note_passes():
    assert not cdi.violations('<li><strong>Cypher:</strong> closed — cards ended 2026-08-08; acquired by Nium (<a href="https://www.cypherhq.io/">source</a>).</li>')

def test_cypher_link_fails():
    assert cdi.violations('<p>closed <a href="https://cypher.example/ref">x</a></p>')

def test_md_recommendation_fails():
    assert cdi.md_violations('Cypher is a good fallback')

def test_md_closed_note_passes():
    assert not cdi.md_violations('Cypher: closed - cards ended 2026-08-08; acquired by Nium ([source](https://www.cypherhq.io/)).')

def test_md_cypherpunk_passes():
    assert not cdi.md_violations('A cypherpunk ethos and Cypherpunk wallets')

def test_md_cypher_link_fails():
    assert cdi.md_violations('closed [x](https://cypher.example/ref)')
