import importlib.util, pathlib
spec = importlib.util.spec_from_file_location('cdi', pathlib.Path(__file__).parents[1] / 'check-dead-issuer.py')
cdi = importlib.util.module_from_spec(spec); spec.loader.exec_module(cdi)

def test_recommendation_fails():
    assert cdi.violations('<p>Best for casual spenders: Cypher. Simple tiers.</p>')

def test_closed_note_passes():
    assert not cdi.violations('<li><strong>Cypher:</strong> closed — cards ended 2026-08-08; acquired by Nium (<a href="https://www.cypherhq.io/">source</a>).</li>')

def test_cypher_link_fails():
    assert cdi.violations('<p>closed <a href="https://cypher.example/ref">x</a></p>')
