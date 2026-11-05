"""Product crops must survive model-specific names without room-plan fallback."""
import copy
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from takeoff_runtime import product_thumbnail, digest


class ThumbnailBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root/'thumbnails').mkdir()
        self.image = self.root/'thumbnails/p2-struct.png'
        Image.new('RGB',(40,50),'white').save(self.image)
        self.crop = dict(row_id='p2-struct',file='drawing.pdf',page=2,
                         image='thumbnails/p2-struct.png',sha256=digest(self.image),
                         source_sha256='current-source')
        self.row = dict(id='Q001',thumbnail='thumbnails/p2-struct.png',sources=[
            dict(file='drawing.pdf',page=1,role='context'),
            dict(file='drawing.pdf',page=2,role='measurement')])
        self.state = dict(documents=[dict(file='drawing.pdf',sha256='current-source')],
            crops={'p2-struct':self.crop},rendered={'D001:1':dict(file='drawing.pdf',page=1,image='plan.png')},
            takeoff=dict(pages=[dict(file='drawing.pdf',page=1,page_type='spatial_plan'),
                               dict(file='drawing.pdf',page=2,page_type='product_drawing')]))

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self):
        return product_thumbnail(self.root,self.state,self.row)

    def test_explicit_registered_crop_with_different_id(self):
        self.assertEqual(self.resolve(),str(self.image))

    def test_stable_item_id_crop_without_explicit_path(self):
        self.row.pop('thumbnail');self.state['crops']={'Q001':self.crop}
        self.assertEqual(self.resolve(),str(self.image))

    def test_child_can_explicitly_share_measurement_crop(self):
        self.row.update(id='A001',parent_id='Q001')
        self.assertEqual(self.resolve(),str(self.image))

    def test_missing_crop_never_uses_rendered_page(self):
        self.state['crops']={}
        self.assertIsNone(self.resolve())
        self.state['rendered']['D001:2']=dict(file='drawing.pdf',page=2,image='product-page.png')
        self.assertIsNone(self.resolve())

    def test_spatial_page_rejected_even_if_source_claims_measurement(self):
        self.crop['page']=1;self.row['sources'][0]['role']='measurement'
        self.assertIsNone(self.resolve())

    def test_context_crop_rejected_even_on_product_page(self):
        self.row['sources'][1]['role']='context'
        self.assertIsNone(self.resolve())

    def test_unrelated_and_stale_sources_rejected(self):
        self.crop['page']=3;self.assertIsNone(self.resolve())
        self.crop['page']=2;self.crop['source_sha256']='another-source'
        self.assertIsNone(self.resolve())

    def test_external_path_and_unregistered_image_rejected(self):
        self.row['thumbnail']='../other-project/thumbnail.png'
        self.assertIsNone(self.resolve())
        self.row['thumbnail']='thumbnails/p2-struct.png';self.state['crops']={}
        self.assertIsNone(self.resolve())

    def test_tampered_image_is_not_silently_substituted(self):
        self.image.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'缓存损坏'):self.resolve()

    def test_explicit_new_crop_takes_priority_over_old_id_crop(self):
        old=copy.deepcopy(self.crop);old['page']=1;self.state['crops']['Q001']=old
        self.assertEqual(self.resolve(),str(self.image))


if __name__=='__main__':unittest.main()
