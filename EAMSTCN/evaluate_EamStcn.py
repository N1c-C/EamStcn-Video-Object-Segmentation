"""
Script to run J&F evaluation on DAVIS-17 and YouTube VOS datasets.
If the dataset has full annotations such as the DAVIS-17 validation set then uncommenting
the calc_results function and the print results lines display the J&F score for each sequence.
Comment/Uncomment the dataset required
Fixed save rate or adaptive save rate set by appropriate flag.

This script expects a saved model  - not the weights. The scripts for loading weights into a model are in the
development folder
"""
from multiprocessing import freeze_support
from torch.utils.data import DataLoader
import time
from EAMSTCN.EamStcn import EamStcn
import torch
from tqdm import tqdm  # progress bar
from datasets.DavisEvalDataset import DAVISEvalDataset
from evaluate.evaluator import EvalEamStcn
from metrics.measurements import *
from utils import *

if __name__ == '__main__':
    freeze_support()

    # Experimental Constants
    TOP_K = 16  # Top k for the affinity calculation - 16 currently the max for Torch on Mac systems
    SAVE_EVERY = 5  # The save rate (SR) save every x frames OR the Additional Save Rate  (ASR) if Adaptive saving set
    ADAPTIVE = False  # Set SAVE_EVERY to 25 or 10
    MEMORY_SIZE = None  # Set if using cache function: Currently not developed fully

    # Pick your model
    MODEL_PATH = '/Users/Papa/Trained Models/EamStcn_A.pth'
    # MODEL_PATH = '/Users/Papa/Trained Models/EamStcn_B.pth'
    # MODEL_PATH = '/Users/Papa/Trained Models/EamStcn_C.pth'
    # MODEL_PATH = '/Users/Papa/Trained Models/EamStcn_Control.pth'
    # MODEL_PATH = '/Users/Papa/Trained Models/EamStcn_Comparison.pth'

    # Save
    name = 'model_C_val'  # The name given to the results csv if calculating the results live
    RESULTS_DIR = f'/Users/Papa/Results/{name}/'  # The location to save the csv file
    OUTPUT_DIR = f'/Users/Papa/Segmentations/{name}/'  # The location to save the segmentations

    # Setup Datasets
    year = 2017
    DAVIS_PATH = '/Users/Papa/'
    YTV_PATH = '/Users/Papa/'

    # Uncomment the set to use - Other data loaders should match the output of the ones in this project
    val_dataset = DAVISEvalDataset(DAVIS_PATH + '/trainval', imset=f'{year}/val.txt')
    # val_dataset = DAVISEvalDataset(DAVIS_PATH + '/test-dev', imset=f'{year}/test-dev.txt')

    NUMBER_OF_WORKERS = 2
    # palette = Image.open('/trainval/Annotations/480p/blackswan/00000.png').getpalette()
    palette = Image.open('/Users/Papa/DAVIS_2_obj/00000.png').getpalette()
    val_test_loader = DataLoader(val_dataset, batch_size=1, shuffle=False, num_workers=NUMBER_OF_WORKERS)

    # Cuda is chosen as default if it is available
    DEVICE = 'mps' if torch.backends.mps else 'cpu'
    if torch.cuda.is_available():
        DEVICE = "cuda"

    # Load the model
    model = torch.load(MODEL_PATH)
    model.eval().to(DEVICE)

    print(f'Number of parameters: {count_parameters(model)}')
    print(f'Number of trainable parameters: {trainable_parameters(model)}')

    status_bar = tqdm(val_test_loader, unit="Batch", position=0, leave=True)

    results = {}
    tot_fr_saved = 0

    for data in status_bar:
        images = data['seq'].to(DEVICE)
        masks = (data['gt_seq'][0]).to(DEVICE)
        info = data['info']
        seq_name = info['name'][0]
        num_of_frs = images.shape[1]  # T
        height, width = images.shape[-2:]
        num_objs = len(info['labels'][0])
        size = info['size']
        status_bar.set_description(f"Sequence {seq_name}")
        evaluator = EvalEamStcn(model, images, num_objs, num_of_frs, info, adaptive=ADAPTIVE, top_k=TOP_K,
                                save_every=SAVE_EVERY, memory_size=MEMORY_SIZE, device=DEVICE)

        # masks shape k T 1 H W  masks[:,0]  k, 1 , h, w  - 1 frame at  a time
        with torch.no_grad():  # Save some memory
            if DEVICE == 'cuda':
                start = time.time()
                evaluator.evaluate(masks, 0, num_of_frs)
            elif DEVICE == 'mps':
                start = time.time()
                evaluator.evaluate(masks, 0, num_of_frs)
            else:
                start = time.time()
                evaluator.evaluate(masks, 0, num_of_frs)

        # Unpad images and resample to the correct shape
        preds = torch.zeros((evaluator.t, 1, *size), dtype=torch.uint8, device=DEVICE)

        # ob_masks used for immediate scoring - shape: n_objs-1 (background) , t, 1, h, w
        ob_masks = torch.zeros(evaluator.preds.shape[0] - 1, *preds.shape)
        end = time.time()

        # Resize the masks to the original image size - Avoidable if no padding is used; for a small loss in accuracy
        for fr in range(evaluator.t):
            prob = unpad(evaluator.preds[:, fr], evaluator.pad)
            prob = F.interpolate(prob, size, mode='bilinear', align_corners=False)
            ob_masks[:, fr] = prob[1:]
            preds[fr] = torch.argmax(prob, dim=0)

        tot_fr_saved += evaluator.model.memory.mem_size()

        ob_masks = ob_masks.detach().cpu().numpy()
        gt = data['gts'][0].detach().cpu().numpy().astype(np.uint8)
        preds = (preds.detach().cpu().numpy()[:, 0]).astype(np.uint8)
        n_objects = evaluator.preds.shape[0]

        # Calculate the J&F for the sequence - Only for datasets with a full set of GT annotations
        results[seq_name] = calc_results(preds, gt, n_objects, start, end)

        # save images - Preds shape t, 1,w , h

        # Uncomment to save prediction masks as png.

        # Suitable for sets starting from frame 00000
        # save_images(preds, OUTPUT_DIR, seq_name, palette)

        # Save images for YouTube: Every five frames and not always starting on 0, so we use the saved frame names
        # save_images_youtube(preds, OUTPUT_DIR, seq_name, palette, info)

        del preds
        del ob_masks
        del gt
        del data

    # Display the J&F results
    print()
    display_metrics(results, RESULTS_DIR, filename=name + f'_DAVIS-{year}')
    print()
    print('Total Frames Saved: ', tot_fr_saved)

